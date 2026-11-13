# github-hookbar

A macOS menu bar indicator that keeps a connection open to a relay of GitHub
webhook deliveries, and runs a command whenever a chosen author opens a pull
request. The command is given the pull request's URL, so it can open the pull
request, announce it, or hand it to whatever should start work on it.
github-hookbar also keeps a file listing those pull requests and their
branches until they are closed, so that other programs can find the pull
request for a branch.

## What the indicator means

- **Pull request arrows**: connected to the relay. The arrows dim for a
  moment when a delivery arrives, and stay dim while deliveries keep
  arriving less than a quarter of a second apart. They turn orange when
  the last delivery arrived more than five seconds after GitHub made it,
  and are otherwise the menu bar's own color. Orange means GitHub or the
  relay is delivering slowly, the delivery waited while the computer
  slept, or this computer's clock is wrong.
- **Circle with three dots**: connecting.
- **Warning triangle**: not connected. The menu says why, and when
  github-hookbar will try again.

## The menu

The menu says whether github-hookbar is connected, and to what, how long
after GitHub made it the last delivery arrived, and names the command it
runs. Below that are the last ten pull requests it ran the command
for, newest first, each marked with a clock while the command runs, a tick
when it exits with status 0, and a cross otherwise. Hovering over one shows
the exit status and everything the command printed. Choosing one opens the
pull request on GitHub.

When a change to the pull request list fails, the menu says so until the
list is next refreshed, and hovering over that line shows the whole error.

**Reconnect** reads the configuration file again and opens a new connection.
**Refresh Pull Request List** rebuilds the pull request list from GitHub, as
described in the section on the pull request list. **Settings…** opens the
settings window.

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

github-hookbar learns when GitHub made a delivery from its
`X-GitHub-Delivery` header, which GitHub sets to a time-based (version 1)
UUID. GitHub gives a redelivered webhook the same ID as the original, so
a redelivery counts as late. A delivery whose ID is not a time-based UUID
returns the arrows to the menu bar's own color.

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
  command and go on the pull request list. Empty means every author.
- **Repository owners**: comma-separated repository owners to accept, for
  both the command and the pull request list. Empty means any owner.
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

## The pull request list

github-hookbar keeps a list of the open pull requests it tracks in
`~/.cache/github-prs.tsv`. A tracked pull request is one whose author and
repository owner the settings choose. Each line of the file is one pull
request: the repository's full name, a tab, the name of the pull request's
branch, a tab, and the pull request's number. For example:

```
octocat/hello-world	fix-typo	1347
```

Each delivery about a tracked pull request updates the list, whatever its
action. When the pull request is open, github-hookbar adds its line, which
replaces any line already there for the same repository and number, so a
redelivered webhook does not add a second line. When the pull request is
closed, whether or not it was merged, github-hookbar removes its line. Lines
about other pull requests, and lines that are not about a pull request, stay
as they are. The relay keeps no history, so a pull request opened or closed
while github-hookbar is not connected changes the list only when the list is
next refreshed, or when another delivery about it arrives. A delivery says
what state the pull request was in when GitHub made it, so redelivering an
old delivery from GitHub can put a closed pull request back on the list until
the next refresh.

**Refresh Pull Request List** replaces the whole list with the open pull
requests that GitHub's search finds for the authors and repository owners in
the settings. It runs the `gh` command in your login shell, so `gh` must be
installed and signed in to GitHub. Refreshing needs at least one author or
repository owner in the settings, because otherwise every open pull request
on GitHub would count as tracked, so the item is disabled without one.
GitHub's search returns at most 1,000 pull requests, so a refresh fails when
the search finds more. When the search fails, the list stays as it was.
Choosing the item while a refresh runs starts the refresh again, and the
earlier search is ignored if it has not finished, so a search that never
finishes does not stop later refreshes. A refresh does not read the old list,
so it also repairs a file that is not UTF-8 text. Without repository owners
in the settings, a refresh also lists the authors' pull requests in
repositories whose webhooks do not reach the relay; those come off the list
only at a refresh after they are closed.

GitHub's search can take a while to include a pull request that was just
opened, or to leave out one that was just merged. A refresh therefore adds
and removes again the pull requests that github-hookbar added or removed in
the ten minutes before the refresh started, and while it ran.

github-hookbar rewrites the file in place while it holds an exclusive lock on
the file, taken with `flock(2)`. A program that holds a shared lock on the
file while reading it therefore never sees the file half written. A program
that changes the file must hold an exclusive lock while it does so, and must
change the file in place rather than replace it. A refresh drops any lines
that other programs added. macOS has no `flock` command, but Homebrew's
`flock` package provides one:

```sh
flock -s ~/.cache/github-prs.tsv cat ~/.cache/github-prs.tsv
```

github-hookbar changes the file on a thread of its own, so a reader that
holds its lock for a long time delays changes to the list but not the menu
bar.

## Building and running

Requires the Xcode command line tools, for `swiftc`. The tests also need
`/usr/bin/jq`, which macOS includes from macOS 15 on. There is no Xcode
project; the app is four Swift files: `main.swift`, which holds the menu bar
app, and `timing.swift`, `configuration.swift`, and `prlist.swift`, which hold
what the tests cover.

```sh
make              # builds ./github-hookbar
make test         # builds and runs the tests in tests/main.swift
./github-hookbar  # runs the menu bar indicator in the foreground
```

github-hookbar logs each time it connects, each time it loses the connection
or fails to connect, each `pull_request` delivery with whether it runs the
command or changes the pull request list, each time the command finishes,
with its exit status and output, one line of output per log line, each
refresh of the pull request list, and each failure to change the list. The
lines go to standard error and to the system log, which keeps them however
github-hookbar was started. They include the URLs of pull requests in private
repositories.
zsh has a `log` command of its own, so give the full path:

```sh
/usr/bin/log show --last 1d --predicate 'subsystem == "local.github-hookbar"'
```

Started from a terminal, it keeps running when the terminal closes; it stops
when **Quit github-hookbar** is chosen.

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
