# github-hookbar

A macOS menu bar indicator that keeps a connection open to a relay of GitHub
webhook deliveries, and runs a command whenever a chosen author opens a pull
request. The command is given the pull request's URL, so it can open the pull
request, announce it, or hand it to whatever should start work on it.

## What the indicator means

- **Pull request arrows**: connected to the relay.
- **Circle with three dots**: connecting.
- **Warning triangle**: not connected. The menu says why, and when
  github-hookbar will try again.

## The menu

The menu says whether github-hookbar is connected, and to what, and names the
command it runs. Below that are the last ten pull requests it ran the command
for, newest first, each marked with a clock while the command runs, a tick
when it exits with status 0, and a cross otherwise. Hovering over one shows
the exit status and everything the command printed. Choosing one opens the
pull request on GitHub.

**Reconnect** reads the configuration file again and opens a new connection.
**Settings…** opens the settings window.

## The relay

GitHub can only deliver a webhook to an address on the public internet, so
deliveries go to a relay, which passes each one on to every program connected
to it. github-hookbar connects to the relay over plain TCP, and expects it
to send each delivery as a frame: the length of an HTTP request, as a 32-bit
big-endian integer, followed by the request exactly as the relay received it,
with its request line, header lines, empty line and body. The next frame
follows straight after. A frame that the end of the connection cuts short is
ignored.

github-hookbar relies on the relay to check each delivery's signature before
passing it on. GitHub signs only the body, so github-hookbar decides what to
do from the body alone, apart from skipping deliveries whose `X-GitHub-Event`
is not `pull_request`.
It reads the body as JSON, so the GitHub webhook's content type must be
`application/json`.

The connection to the relay is neither encrypted nor authenticated, and
webhook payloads can describe private repositories, so the relay should be
reachable only from computers you trust, for example over a private network.

The relay keeps no history, so a pull request opened while github-hookbar is
not connected does not run the command. Redelivering the webhook from GitHub,
or running the command by hand, covers it.

## Reconnecting

The relay sends nothing between deliveries. github-hookbar turns on TCP
keepalive, so a connection that dies without closing, for example while the
computer sleeps, is noticed within about a minute and a half of idling.

When the connection ends, github-hookbar tries again after one second, then
after two, four, and so on, up to a minute apart, and goes back to one second
once it has stayed connected for longer than the wait before it. When the
computer wakes while github-hookbar is not connected, it tries again straight
away.

## Configuration

github-hookbar keeps its configuration in
`~/.config/github-hookbar/config.ini`, or in the file given with
`--config PATH`. The settings window, which opens by itself when the file does
not exist yet, has a field for each setting:

- **Relay**: the host and port where the relay accepts connections.
- **Authors**: comma-separated GitHub logins whose pull requests run the
  command. Empty means every author.
- **Repository owners**: comma-separated repository owners to accept. Empty
  means any owner.
- **Actions**: which pull request actions run the command. The default,
  `opened`, covers new pull requests, including drafts, and is what an empty
  field means.
- **Command**: the command line to run.

**Save** edits the file in place. Only the lines of the fields you changed are
rewritten, so comments, layout, and anything changed in the file while the
window was open stay as they were, and a setting the file lacks is added after
the others in its section. A file that is a symbolic link stays one. The new
settings are checked before anything is written; when they are wrong, the
window says why and the file is left alone. Once saved, github-hookbar
reconnects with them. **Edit File…** opens the file itself.

The file is in INI format; see [config.example.ini](config.example.ini). The
window's settings are, in order, `address` in `[relay]`, `authors`, `orgs` and
`actions` in `[github]`, and `run` in `[command]`. github-hookbar refuses a
file that sets any other key, or sets one twice, so a misspelled or repeated
key cannot quietly widen what runs the command. The window says so when it
opens.

The configuration is read when github-hookbar starts, and again whenever it
connects to the relay, including when **Reconnect** is chosen and after
**Save**. When the file cannot be read, the menu says why, and github-hookbar
tries again when **Reconnect** is chosen or the computer wakes.

## The command

github-hookbar adds the pull request's URL to the end of the command line, as
its last argument, so a command line of `open` opens the pull request in a
browser. launchd starts login items with almost no environment, so
github-hookbar runs the command line in your login shell, as an interactive
login shell. It then sees the same `PATH` and other variables a terminal sees.
The last 2,000 characters of the command's standard output and standard error
are kept for the menu, and nothing is written to its standard input.

A delivery redelivered from the webhook's page on GitHub runs the command
again, which is how to recover a pull request opened while github-hookbar was
not connected.

## Building and running

Requires the Xcode command line tools, for `swiftc`. There is no Xcode
project; the whole app is one Swift file.

```sh
make              # builds ./github-hookbar
./github-hookbar  # runs the menu bar indicator in the foreground
```

github-hookbar writes a line to standard error each time it connects, each
time it loses the connection or fails to connect, and each time it runs the
command. Started from a terminal, it keeps running when the terminal
closes; it stops when **Quit github-hookbar** is chosen.

## Installing as a login item

```sh
make install
```

This copies the binary to `~/.local/bin/github-hookbar`, generates a launchd
user agent at `~/Library/LaunchAgents/local.github-hookbar.plist` from the
template in this directory, and starts it. The agent starts the indicator at
login and restarts it if it crashes; quitting from the menu (a clean exit)
does not trigger a restart. Running `make install` again rebuilds, reinstalls,
and restarts the running indicator.

```sh
make uninstall
```

This stops the indicator and removes both the agent plist and the installed
binary. The configuration file is left alone.
