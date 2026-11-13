# Runs commands that other programs send to this shell, but only while the
# shell is idle at an empty primary prompt. Nothing is typed into the
# terminal: the line editor's handler runs the command itself. Senders find
# the shell by its process ID, which iTerm2 reports as the session's jobPid
# while the shell is the terminal's foreground process. Source this file
# from ~/.zshrc; the sender is the iterm-run script. ITERM_RUN_DIR, if set,
# names the directory of the FIFOs. README.md describes the protocol.

[[ -o interactive ]] || return
zmodload zsh/system
zmodload -F zsh/files b:zf_chmod b:zf_rm
autoload -Uz add-zsh-hook add-zle-hook-widget

typeset -g _iterm_run_reply_to= _iterm_run_cmd=
typeset -gi _iterm_run_searching=0 _iterm_run_exiting=0
typeset -g _iterm_run_key=$'\e[2026~'

# Each request is one line: the path of the sender's reply FIFO, a tab, and
# the command. The reply is "ran" or "busy". Fails if the sender has gone.
_iterm_run_reply() {
  local fd
  sysopen -w -o cloexec,nonblock -u fd -- $1 2>/dev/null || return
  print -r -u $fd -- $2
  exec {fd}>&-
}

# Sets _iterm_run_reply_to and _iterm_run_cmd from a request line. Fails
# for a line that names no sender's reply FIFO in this shell's directory,
# however the sender spells the directory.
_iterm_run_parse() {
  _iterm_run_reply_to=${1%%$'\t'*} _iterm_run_cmd=${1#*$'\t'}
  [[ $1 == *$'\t'* && ${_iterm_run_reply_to:t} == reply-<-> &&
     ${_iterm_run_reply_to:h:A} == $_iterm_run_dir && -p $_iterm_run_reply_to ]]
}

_iterm_run_refuse_waiting() {
  local line
  while IFS= read -t 0 -r -u $_iterm_run_fd line; do
    _iterm_run_parse $line && _iterm_run_reply $_iterm_run_reply_to busy
  done
}

_iterm_run_status() { return $1 }

_iterm_run_add_history() {
  local f
  [[ -o hist_ignore_space && $_iterm_run_cmd == ' '* ]] && return
  for f in zshaddhistory $zshaddhistory_functions; do
    (( $+functions[$f] )) && { $f "$_iterm_run_cmd"$'\n' || return }
  done
  print -rs -- $_iterm_run_cmd
}

# Marks output that did not end with a newline, as zsh does before a
# prompt, and leaves the cursor at the start of an empty line.
_iterm_run_prompt_sp() {
  [[ -o prompt_sp && -o prompt_cr ]] || return
  local mark=${(%)PROMPT_EOL_MARK-%B%S%#%s%b}
  print -rn -- $mark
  printf '%*s\r \r' $(( COLUMNS - ${#${(S)mark//$'\e'\[*m/}} )) ''
}

# Types the private key sequence into this terminal without echoing it.
# Its binding does nothing, but reading it lets the line editor act on a
# pending exit.
_iterm_run_type_key() {
  perl -MPOSIX -e '
    open my $tty, "+<", "/dev/tty" or exit 1;
    my $t = POSIX::Termios->new;
    $t->getattr(fileno $tty) or exit 1;
    my $lflag = $t->getlflag;
    $t->setlflag($lflag & ~ECHO);
    $t->setattr(fileno $tty, TCSANOW);
    ioctl($tty, 0x80017472, $_) for split //, $ARGV[0];  # TIOCSTI
    $t->setlflag($lflag);
    $t->setattr(fileno $tty, TCSANOW);
  ' -- $_iterm_run_key
}
_iterm_run_noop() { }
zle -N _iterm_run_noop

# Runs _iterm_run_cmd from inside the line editor the way zsh runs a typed
# command: shown on the prompt line, added to history, with bracketed paste
# off, and with the preexec and precmd hooks, which see the command's exit
# status. "zle -I" moves output below the command line and restores the
# shell's normal terminal settings; after the command it moves below a
# prompt that zsh redrew because the window was resized. The line editor
# then draws a newly expanded prompt.
#
# Differences from typing the command remain, because it runs inside a
# function: $? afterwards is unchanged, "typeset" and "local" make local
# variables, an EXIT trap fires when the command ends, and $0 names this
# function. README.md lists them all.
_iterm_run_execute() {
  local _iterm_run_f _iterm_run_st _iterm_run_wrapped=()
  _iterm_run_exiting=0
  {
    BUFFER=$_iterm_run_cmd
    zle -R
    zle -I
    # Asks iTerm2 to bring this session's window, tab and pane forward.
    [[ $TERM_PROGRAM == iTerm.app ]] && print -rn -- $'\e]1337;StealFocus\a'
    _iterm_run_add_history
    for _iterm_run_f in preexec $preexec_functions; do
      (( $+functions[$_iterm_run_f] )) &&
        $_iterm_run_f $_iterm_run_cmd $_iterm_run_cmd $_iterm_run_cmd
    done
    # An exit only takes effect when the line editor reads a key, so these
    # wrappers record that the command started one. An exit declined
    # because jobs are running is reported as an error.
    for _iterm_run_f in exit logout bye; do
      (( $+functions[$_iterm_run_f] )) && continue
      functions[$_iterm_run_f]="_iterm_run_exiting=1
        { builtin $_iterm_run_f \"\$@\" } always { (( ! TRY_BLOCK_ERROR )) || _iterm_run_exiting=0 }"
      _iterm_run_wrapped+=$_iterm_run_f
    done
    print -rn -- $zle_bracketed_paste[2]
    eval $_iterm_run_cmd </dev/tty
  } always {
    _iterm_run_st=$?
    # After Ctrl-Z and fg, a forked copy of the shell finishes the command.
    [[ $sysparams[pid] == $$ ]] || exit $_iterm_run_st
    (( $#_iterm_run_wrapped )) && unfunction $_iterm_run_wrapped
    print -rn -- $zle_bracketed_paste[1]
    BUFFER=
    if (( ! _iterm_run_exiting )); then
      for _iterm_run_f in precmd $precmd_functions; do
        (( $+functions[$_iterm_run_f] )) &&
          { _iterm_run_status $_iterm_run_st; $_iterm_run_f }
      done
      _iterm_run_prompt_sp
    fi
    zle -I
    # An error or Ctrl-C ends the command, as it would at the prompt,
    # without also ending the line editor's current wait for keys.
    TRY_BLOCK_ERROR=0 TRY_BLOCK_INTERRUPT=0
    if (( _iterm_run_exiting )); then
      # Once an exit is pending, zsh skips the zshexit hooks and returns
      # from every function as soon as a function call returns, so builtins
      # clean up before the one call that wakes the line editor.
      zf_rm -f -- $_iterm_run_fifo $_iterm_run_fifo.lock
      _iterm_run_type_key
    fi
  }
}

# Called by the line editor whenever a request arrives while it is reading
# keys. The line editor is idle when it is at the primary prompt with an
# empty line, is not in a recursive edit or incremental search, has no
# numeric argument pending, and has read no keys of the next key sequence
# or of a widget's argument.
_iterm_run_receive() {
  local _iterm_run_line
  IFS= read -r -u $_iterm_run_fd _iterm_run_line || return
  _iterm_run_parse $_iterm_run_line || return
  if [[ -n $_iterm_run_cmd && $CONTEXT == start && -z $BUFFER$PREBUFFER$KEYS &&
        $ZLE_RECURSIVE == 0 && $_iterm_run_searching == 0 && ${+NUMERIC} == 0 ]]
  then
    _iterm_run_reply $_iterm_run_reply_to ran && _iterm_run_execute
  else
    _iterm_run_reply $_iterm_run_reply_to busy
  fi
}
zle -N _iterm_run_receive

_iterm_run_search_start() { _iterm_run_searching=1 }
_iterm_run_search_end() { _iterm_run_searching=0 }

# While a command runs, the FIFO is unreadable, so senders get "busy" at
# once; requests written just before that are refused too.
_iterm_run_busy() {
  zf_chmod 000 $_iterm_run_fifo
  _iterm_run_refuse_waiting
}
_iterm_run_idle() {
  _iterm_run_refuse_waiting
  zf_chmod 600 $_iterm_run_fifo
}

_iterm_run_exit() {
  [[ $sysparams[pid] == $$ ]] || return
  _iterm_run_refuse_waiting
  zf_rm -f -- $_iterm_run_fifo $_iterm_run_fifo.lock
}

if (( ! ${+_iterm_run_fd} )); then
  typeset -g _iterm_run_dir=${ITERM_RUN_DIR:-$(getconf DARWIN_USER_TEMP_DIR)iterm-run}
  mkdir -p -m 700 -- $_iterm_run_dir || return
  _iterm_run_dir=${_iterm_run_dir:A}
  typeset -g _iterm_run_fifo=$_iterm_run_dir/$$
  # Senders wait on this lock to learn that the shell exited, however it
  # exited.
  : >>| $_iterm_run_fifo.lock
  zf_chmod 600 $_iterm_run_fifo.lock
  zsystem flock -f _iterm_run_lock_fd $_iterm_run_fifo.lock || return
  zf_rm -f -- $_iterm_run_fifo
  mkfifo -m 600 -- $_iterm_run_fifo || return
  # Opened for reading and writing, so that the shell always counts as a
  # reader and a sender's nonblocking open succeeds.
  sysopen -r -w -o cloexec -u _iterm_run_fd -- $_iterm_run_fifo || return
  zle -F -w $_iterm_run_fd _iterm_run_receive
  for _iterm_run_keymap in emacs viins vicmd; do
    bindkey -M $_iterm_run_keymap $_iterm_run_key _iterm_run_noop
  done
  unset _iterm_run_keymap
  add-zle-hook-widget isearch-update _iterm_run_search_start
  add-zle-hook-widget isearch-exit _iterm_run_search_end
  # These run before other hooks, because an error in a hook makes zsh skip
  # the hooks after it, which would leave the shell refusing requests.
  preexec_functions=(_iterm_run_busy $preexec_functions)
  precmd_functions=(_iterm_run_idle $precmd_functions)
  add-zsh-hook zshexit _iterm_run_exit
fi
