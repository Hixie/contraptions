# github-hookbar

A macOS menu bar indicator that keeps a connection open to a relay of GitHub
webhook deliveries, and runs a command whenever a chosen author opens a pull
request. The command is given the pull request's URL, so it can open the pull
request, announce it, or hand it to whatever should start work on it.
github-hookbar also keeps a file listing those pull requests and their
branches until they are closed, so that other programs can find the pull
request for a branch. It can also follow a stream of server-sent events and
keep a value from those events in a file, as described in the section on
following an event stream, and run another command whenever the computer
switches between battery and wall power, as described in the section on
the power source.

## What the indicator means

The indicator shows the state of the connection to the relay. The menu shows
the state of the event stream.

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

When the settings name a power command, the menu names it too. Once it has
run, the menu says which power source it last ran for, marked as the pull
requests are, and hovering over it shows the exit status and everything it
printed.

When a change to the pull request list fails, the menu says so until the
list is next refreshed, and hovering over that line shows the whole error.

When the settings name an event stream, the menu also says whether
github-hookbar is connected to it, and when it last wrote the value file, or
why it could not.

**Reconnect** reads the configuration file again and opens new connections.
**Refresh Pull Request List** rebuilds the pull request list from GitHub, as
described in the section on the pull request list. **Settings…** opens the
settings window. Holding Option turns **Reconnect** into **Send the Train**,
which sends the freight train described below straight away, as
Command-Option-R does while the menu is open.

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

The relay keeps no history, so github-hookbar misses the deliveries GitHub
makes while it is not connected. When it connects again, it catches up. It
refreshes the pull request list, as **Refresh Pull Request List** does. It
also asks GitHub, with the `gh` command, for the open pull requests opened
since ten minutes before it last heard from the relay, by connecting or by a
delivery arriving, but not before it first connected. It runs the command
for each of those that the settings choose, unless it has already run the
command for that pull request.

With authors in the settings, github-hookbar lists each author's open pull
requests, which GitHub shows as soon as they are opened. An author that is
not a GitHub user, such as a bot, has none. With only repository
owners, it uses GitHub's search, which can take a while to include a pull
request that was just opened. With neither, it does not look. Catching up
runs the command only when the settings' actions include `opened`, since
GitHub says when each pull request was opened but not when other actions
happened. When a list holds 100 pull requests that were all opened since
then, there may be more than GitHub returned, so the catch-up fails. When
catching up fails, the menu says why until it next succeeds, and
github-hookbar tries again the next time it connects.
Choosing **Reconnect** or saving the settings while connected opens a new
connection without catching up, so new settings do not run the command for
pull requests opened before they were saved.

## Reconnecting

The relay sends nothing between deliveries. github-hookbar turns on TCP
keepalive, so a connection that dies without closing, for example while the
computer sleeps, is noticed within about a minute and a half of idling.

An event stream that sends nothing for a minute is taken to have died in the
same way, so a stream that can be quiet for longer should send comments or
other events to keep it open.

When a connection ends, github-hookbar tries again after one second, then
after two, four, and so on, up to a minute apart, and goes back to one second
once a connection has lasted longer than the next wait would be. The relay
and the event stream each keep their own waits. When the computer wakes
while github-hookbar is not connected, it tries again straight away.

## Configuration

github-hookbar keeps its configuration in
`~/.config/github-hookbar/config.ini`, or in the file given with
`--config PATH`. The settings window, which opens by itself when the file does
not exist yet, has a field for each setting, in four groups. Each group
says what it is for, and each field says what it holds.

**Webhook relay**

- **Address**: the host and port where the relay accepts connections.

**Pull requests**

- **Authors**: comma-separated GitHub logins whose pull requests run the
  command and go on the pull request list. Empty means every author.
- **Repository owners**: comma-separated repository owners to accept, for
  both the command and the pull request list. Empty means any owner.
- **Actions**: which pull request actions run the command. The default,
  `opened`, covers new pull requests, including drafts, and is what an empty
  field means.
- **Command**: the command line to run.

**Event stream**

- **URL**: the `http` or `https` URL of a stream of server-sent events to
  follow. Empty means none, in which case the next two fields must be empty
  too.
- **Value**: where the value is in each event's data, as described in the
  section on following an event stream.
- **Save to**: the file to keep the value in. A leading `~` stands for your
  home directory.

**Power source**

- **Command**: the command line to run when the computer switches between
  battery and wall power. Empty means none.

**Save** edits the file in place. Only the lines of the fields you changed are
rewritten, so comments, layout, and anything changed in the file while the
window was open stay as they were, and a setting the file lacks is added after
the others in its section. A file that is a symbolic link stays one. The new
settings are checked before anything is written; when they are wrong, the
window says why and the file is left alone. Once saved, github-hookbar
reconnects with them. **Edit File…** opens the file itself.

The file is in INI format; see [config.example.ini](config.example.ini). The
window's settings are, in order, `address` in `[relay]`, `authors`, `orgs` and
`actions` in `[github]`, `run` in `[command]`, `url`, `path` and `file` in
`[stream]`, and `run` in `[power]`. github-hookbar refuses a file that sets
any other key, or sets one twice, so a misspelled or repeated key cannot
quietly widen what runs the command. The window says so when it opens.

The configuration is read when github-hookbar starts, when **Reconnect** is
chosen, and after **Save**. Attempts to connect again after a connection ends
use the settings already read. When the file cannot be read, the menu says
why, and github-hookbar reads it again when **Reconnect** is chosen or the
computer wakes.

## The command

github-hookbar adds the pull request's URL to the end of the command line, as
its last argument, so a command line of `open` opens the pull request in a
browser. launchd starts login items with almost no environment, so
github-hookbar runs the command line in your login shell, as an interactive
login shell. It then sees the same `PATH` and other variables a terminal sees.
The last 2,000 characters of the command's standard output and standard error
are kept for the menu, and nothing is written to its standard input.

A delivery redelivered from the webhook's page on GitHub runs the command
again, even for a pull request it has already run for.

## Following an event stream

github-hookbar can keep a connection open to a stream of server-sent events,
the format that a web page's `EventSource` reads, and keep a value from those
events in a file, so that other programs can show it. The settings name the
stream's URL, a path to the value in each event's data, and the file.

Each event's data is read as JSON, and the path picks a string out of it.
The path is a list of keys separated by dots. Each key names a member of an
object or, as a number counting from 0, an element of an array. For
example, given an event whose data is

```json
{"message": {"text": "Back at 3pm", "tags": ["away", "lunch"]}}
```

the path `message.text` picks `Back at 3pm`, and `message.tags.1` picks
`lunch`. A path cannot name a key that contains a dot.

github-hookbar reads each event's data only as far as it needs to. It skips
whatever is off the path without keeping it, and stops reading once it has
found the string or knows there is none. Events without a string at the path
are ignored, whatever their event type. Data that is not JSON may give a
string or not. Fields other than `data`, such as `id` and `retry`, are
ignored too, so github-hookbar does not ask the server to resume where a
previous connection left off. A string of more than 32 MB, counted as UTF-8,
ends the connection. The file holds exactly the string, as UTF-8, with no
line ending added, so an empty string leaves it empty. When an event's value
differs from what the file holds, github-hookbar rewrites the file in place
while it holds an exclusive lock on the file, taken with `flock(2)`, as it
does for the pull request list described below. A program that holds a shared
lock on the file while reading it, for example with `flock -s`, therefore
never sees it half written. github-hookbar makes the file and its directory
if there are none, and changes the file on a thread of its own, so a reader
that holds its lock for a long time does not hold up the menu bar. The file
keeps its last value while github-hookbar is not connected to the stream.

The server must answer with status 200 and the content type
`text/event-stream`. A stream that ends, or that the server answers any
other way, is opened again as described in the section on reconnecting.
The menu and the log show the stream's URL without its query, user name, or
password, which can hold secrets.

**Reconnect** and **Save** open the stream again. When its settings did not
change, the menu goes on saying when the file was last written.

## The power source

github-hookbar asks macOS to say whenever the computer switches between a
limited power source, such as its battery, and an unlimited one, such as a
wall outlet. Each time it does, github-hookbar runs the power command from
the settings, if they name one, with `battery` or `wall` as its last
argument. It runs the command line with `/bin/sh`, which reads no startup
files, rather than in your login shell like the pull request command, so
that the command starts quickly even when the computer is busy, as it can
be when work paused on battery resumes. The command, and every program it
starts, sees only the environment github-hookbar was started with. Started
as a login item, that gives it a `PATH` of
`/usr/bin:/bin:/usr/sbin:/sbin`. Name programs outside those folders by
their full paths, or set `PATH` at the start of the command line. The end
of the command's output is kept for the menu, as for the pull request
command. It also runs the command once when it starts, and when saved
settings change the command, with the power source at that moment, and
checks the power source when the computer wakes, in case it switched while
the computer slept. It runs the power command once at a time: a switch
while the command runs waits until it finishes, and only the latest such
switch runs the command then, so each run finishes before the next starts
and the last run is for the latest power source.

## The freight train

Whenever github-hookbar writes a new value to the value file, a freight
train rolls along the bottom of the main display, the one with the menu bar,
from beyond its right edge to beyond its left, with the value lettered along
the sides of its cars. An SBB Re 620 electric locomotive in SBB Cargo's
livery pulls the cars: red cab ends, a blue side lettered "cargo", and a
grey roof with its pantograph raised. Its six wheels on each side turn as it
goes, and the cars rock on their springs.
Under and over the train runs a railway line: a track, and above it an
overhead line on masts, a contact wire hung from a sagging catenary wire,
which the raised pantograph runs along, its arms flexing at their joints as
the engine rocks so that its head stays against the wire, now and then
sparking against it.
Sparks come in bursts that begin at random, about once every eight seconds.
Each spark in a burst has a one in two chance of being followed by another
30 to 150 milliseconds later, and separately a one in four chance of being
followed by another 100 to 1,500 milliseconds later, so a burst has four
sparks on average. A spark that comes while the one before it still shows
cuts it short, which makes the arc flicker. The line is laid from the right edge
of the screen, starting slowly and speeding up until it reaches the left
edge. The train comes in once the line is a quarter of the screen ahead,
when the line is going twice as fast as the train, so the line stays ahead
of it. Behind the train, the line is taken up a little behind the last car.
Both ends of the line have soft edges.

The engine leads, so the value reads in order from the front of the train to
the back. Each car carries as many whole words as fit in 150 points of
lettering, and a word too long for one car is split across as many as it
needs. A train pulls at most 40 cars; a longer value is cut short, and the
last car says so with "…". Line breaks count as spaces. The train goes at
170 points a second, slowly enough to read the cars, so it takes as long as
it needs for its whole length to cross the screen. It passes over
everything, the Dock included, and clicks go through it to whatever is
underneath. A new value sends a new train in place of any train still on its
way. A value that is empty or only white space sends none, and leaves a
train already on its way alone.

A value counts as new when it differs from what the value file held, so a
value that arrives while the file already holds it, as it does each time
github-hookbar connects to the stream, sends no train. **Send the Train**
sends one carrying the last value the stream gave, or, when there is none or
it is only white space, one saying that there is no message.

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
while github-hookbar is not connected changes the list when github-hookbar
catches up after connecting again, as described in the section on the relay,
or when another delivery about it arrives. A delivery says
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

Requires the Xcode command line tools, for `swiftc`. Catching up after
reconnecting, and the tests, also need `jq`, which macOS includes from macOS
15 on. There is no Xcode project; the app is eleven Swift files: `main.swift`
and `settingswindow.swift`, which hold the menu bar app and its settings
window, and `timing.swift`, `configuration.swift`, `prlist.swift`,
`lockedfile.swift`, `connection.swift`, `stream.swift`, `train.swift`,
`power.swift`, and `run.swift`, which hold what the tests cover, apart from
the train's window.

```sh
make              # builds ./github-hookbar
make test         # builds and runs the tests in tests/main.swift
./github-hookbar  # runs the menu bar indicator in the foreground
```

github-hookbar logs each time it connects to the relay or the event stream,
each time it loses a connection or fails to connect, each time it writes the
value file, each new reason it cannot write the value file, each time the
value file turns out to hold the latest value already after such a failure,
each `pull_request` delivery with whether it runs the command or changes the
pull request list, each time the command finishes, with its exit status and
output, one line of output per log line, each refresh of the pull request
list, each failure to change the list, each time it looks for pull
requests opened while it was not connected or fails to, and each switch
between battery and wall power, with when the power command finishes, its
exit status and output. The lines go to
standard error and to the system log, which keeps them however github-hookbar
was started. They include the URLs of pull requests in private repositories.
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
