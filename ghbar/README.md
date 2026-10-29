# ghbar

A macOS menu bar indicator that shows whether the checks GitHub runs on one
branch of one repository are passing. The indicator watches the head commit of
the branch, so it follows the branch as new commits land on it.

The checks on a commit are every GitHub Actions workflow run on it and every
commit status set on it, whatever started them: a push, a schedule, another
workflow finishing, or a person.

## What the indicator means

The shape changes along with the color, so the indicator still reads when red
and green do not.

- **Green tick**: every check that reached a verdict passed.
- **Red cross in an octagon**: at least one check failed. A check that timed
  out, failed to start, or asked for action counts as a failure, and so does
  a job cancelled while a runner was running it. A workflow run fails as soon
  as one of its jobs does, before the rest of it finishes. A job cancelled
  while it waited for a runner neither passes nor fails: GitHub cancels a
  waiting run or job when a newer one takes its place in a concurrency
  group, and that says nothing about the commit. A cancelled workflow run
  therefore takes the state of its jobs: it fails when one of them failed or
  was stopped while it ran, passes when the rest passed, and otherwise
  neither passes nor fails.
- **Orange clock**: no check has failed, and at least one is still queued or
  running.
- **Gray dash**: GitHub reported no checks on the commit, or reported only
  checks that were skipped or neutral, or runs cancelled before a runner ran
  any of their jobs.
- **Gray question mark**: the state is not known, because the last request to
  GitHub did not work. The menu says why.
- **Gray gear**: no repository has been chosen yet.

Next to the indicator is the repository's name, so several indicators can be
told apart at a glance. The name can be replaced with one of your own, or
turned off, in the settings.

## The menu

Clicking the indicator opens a menu that names the repository and branch, sums
up the checks, and names the head commit. Below that come the checks that are
failing or still running, each of which opens its own page on GitHub when
clicked; a workflow run that has a failed job names the job. Everything else
is in the **All Checks** submenu, which lists every check on the commit,
failures first. **Open Checks on GitHub** opens the head commit's checks page.

The line above the last separator names the indicator's number, when it last
asked GitHub, and how much of the hourly request allowance is left. When
the indicator listens to a webhook relay, the line before it says whether the
relay is connected; while it is not, the line's tooltip says why.

The menu keeps changing while it is open, so a check that finishes while you
are reading moves as it finishes.

## When the branch turns red

A small plane flies out from behind the menu bar item towing a banner that
says so, for example "labs is red", using the name shown in the menu bar. It
dives away to the left, turns round, and climbs back in behind the menu bar
item it came from. Each flight is different: how steeply it dives, how far out
and down it goes, whether it loops the loop or hops on the way out, whether it
turns round with a half loop up and a roll or a roll and a half loop down, and
whether it loops again on the way back are all chosen afresh. It flies at 360
points a second, slowly enough to read the banner, and each flight is sized to
cover 1,800 to 2,250 points, which with the banner following it takes five to
seven seconds, going as far out as that allows unless the screen runs out
first. At least a third of every flight is spent cruising, flying straight and
no steeper than 30 degrees; when the choices would not allow that, the dive
and then the climb are made shallower, and then the aerobatics are left out
one at a time. The plane is 41 points high and the banner 26. Nothing about it
can be clicked; the pointer goes straight through it.

The flight is worked out before it starts, as Core Animation keyframes, and
the window server plays keyframes against the clock. On a busy machine the
flight drops frames rather than slowing down. The banner is cut into narrow
strips, each following the plane along the same path a little later, so it
bends around the loops behind the plane. Its back carries the lettering
mirrored, so it reads correctly once the plane has turned round.

It flies when a branch that was passing starts failing, and again when a
branch that is already failing breaks in some new way, so that a second thing
going wrong is not silent. It does not fly for the first answer GitHub gives
about a branch, after an indicator starts or after its settings change to
another branch: a branch that was already red is not a branch that has just
turned, and logging in should not send a plane round for every indicator that
comes back.

Running `ghbar --banner` sends the plane round straight away and quits when it
has landed. Holding Option in the menu turns **New Indicator** into **Fly the
Banner**, which does the same without leaving the menu bar; Command-Option-N
does it too.

## Handing a red branch to an agent

An indicator can also hand a branch that turns red to a coding agent, which
is asked to "select an appropriate worktree and fix the red BRANCH on
OWNER/NAME", followed by a link to the commit's checks page and a report of
what failed. Before handing the branch off, the indicator asks GitHub for
that report: each failed job, the step it failed at, and a link to it; each
failed commit status; and the commits since the last commit it saw pass,
newest first. Whatever GitHub will not say is left out. The agent's
session starts in the session folder chosen in the settings, or in your home
folder when none is chosen. A relative folder is taken from your home folder.

This is off unless **When it turns red** in the settings says otherwise. It
can say either of these:

- **Start a Codex session to fix it.** The Codex app comes to the front with
  the new session, which starts working without anyone pressing anything.
  The session begins with the request, which runs outside the app with
  the Codex CLI's configured sandbox and approval settings. When that
  turn finishes, ghbar opens the session in the app, where a second
  message, "Execute.", runs with the app's sandbox and approval settings.
- **Open a Claude session, ready to send.** The Claude app comes to the
  front with a new Code session that has the request written in. The Claude
  app does not send a request that arrives this way, so it waits for you to
  send it.

A branch is handed off once each time it turns red, and not again until it
has passed, even when new commits land on it while it is still red. As with
the banner, nothing is handed off for the first answer about a branch.

The menu names the branch the last session was started for, and when. For
Codex, choosing that line opens the session. When a session could not be
started, the line says so, and its tooltip says why.

Running `ghbar --hand-off` hands the branch to the chosen agent straight
away, whatever state the branch is in, and exits.

## Settings

**Settings…** opens a window with the repository, the branch, the name to draw
in the menu bar, how often to ask GitHub, which agent to hand the branch to
when it turns red, the folder that agent's session starts in, and the webhook
relay to hear about changes from, written as `host:port`. The
repository can be written as `owner/name`, or pasted in any of the forms GitHub
hands out: the address of the repository, the address of a branch, or the SSH
remote. Pasting the address of a branch fills in the branch as well.

Settings are remembered per indicator, in `~/Library/Preferences`, under
`local.ghbar.` followed by the indicator's number.

## Running several indicators

Each running indicator holds one instance number, from 1 upwards. The number
picks the settings it reads, the lock file it holds, and the launchd agent it
writes, so two indicators never tread on each other. **New Indicator** in the
menu starts another copy, which takes the lowest number no other indicator is
using; running the program again from a shell does the same thing.

An indicator started with `--instance N` takes that exact number. If another
indicator is already running as N, the new one says so and exits, which is what
keeps a login from starting a second copy of an indicator you already have.

## Starting at login, and stopping

Every ordinary launch writes `~/Library/LaunchAgents/local.ghbar.N.plist` and
hands the indicator over to that launchd agent. The handoff makes launchd
responsible for the running indicator immediately, rather than only after the
next login, so it can start the indicator again after an abnormal exit.
Several indicators mean several agents, one per number, and every one of them
starts at login. The agent points at wherever the program was run from, so
running the copy in `~/.local/bin` is what registers that copy.

**Disable and Quit** deletes that indicator's agent, stops its launchd job, and
exits. The indicator stops coming back at login. Its settings stay behind, so
starting an indicator with that number again picks up where it left off.

**Quit ghbar** exits without touching the agent, so the indicator returns at
the next login.

## The GitHub token

Public repositories need no token. Private ones do, and a token also raises the
hourly request allowance from sixty to five thousand.

launchd starts login items with almost no environment, so an indicator started
at login cannot read the `GH_TOKEN` your terminal reads. Rather than keep a
copy of the token, the app asks your login shell for one: it runs your shell as
an interactive login shell, which reads the same profile files an interactive
terminal reads, and takes `GH_TOKEN`, then `GITHUB_TOKEN`, then whatever
`gh auth token` prints. The settings window names which of those the token came
from. The answer is remembered until GitHub rejects it, at which point the app
asks the shell again.

The indicator reads the checks through the Actions API, which lists a
commit's workflow runs and their jobs, and the commit status API. A
fine-grained token needs read access to **Actions**, **Commit statuses** and
**Contents**; the last gives the commit messages. GitHub does not offer such
a token the **Checks** permission at all, which is why the indicator does not
use the check runs API: in a private repository, that API refuses such a
token. Check suites run by apps other than GitHub Actions are therefore not
counted. Deliveries about them are passed over too, so that the indicator
says the same with a relay as without one.

Adding a permission to a token an organization has already approved can put
that token back in front of the organization's owners, so a permission that
has been added but not yet approved reads exactly like one that was never
added.

## Requests and the rate limit

Each time it asks, the indicator makes these requests: the branch's combined
commit status, which also names the commit the branch is on; the workflow
runs on that commit; the jobs of each workflow run that has not finished, so
that a job that has failed is seen before its run finishes; the jobs of each
workflow run that was cancelled, which say whether a runner was running a job
it stopped, asked for once and again only when the run changes; and, once per
commit, the commit's message. Every request carries the ETag of the previous
answer to it. With a token, GitHub charges nothing for an answer of "nothing
has changed", so an idle branch costs almost nothing even when polled.
Without one, every request counts against the sixty an hour, and polling a
branch that has a workflow running every minute spends them in well under an
hour.

While a webhook relay is connected, the indicator asks for a starting point
on each connection, when deliveries show that one went missing, and once
more after an answer that may lag them, as the next section describes.

## Hearing about changes from a webhook relay

GitHub can only deliver a webhook to an address on the public internet. A
relay receives the deliveries there and passes each one on to every program
connected to it, which brings them to a computer on a private network. An
indicator that is given a relay in its settings listens to it, and hears
about a branch's checks as they change, rather than asking GitHub at an
interval.

The indicator connects to the relay over plain TCP. It expects the relay to
send each delivery as a frame: the length of an HTTP request, as a 32-bit
big-endian integer, followed by the request exactly as the relay received
it. The webhook that feeds the relay has to cover the repository, use the
`application/json` content type, and send check run, check suite, status
and push events.

While the relay is connected, the indicator follows the branch from the
deliveries alone:

- a push to the branch moves the indicator to the new commit, and gives it
  the commit's message;
- a check run that starts makes its suite run, and one that fails makes its
  suite fail;
- a check suite that finishes takes the state it finished in;
- a check run or check suite that is cancelled asks GitHub, since only
  GitHub's answer says whether a runner was running the job it stopped, and
  GitHub is asked again at the interval until an answer shows the suite's
  latest attempt finished, or no longer lists the suite; a push that moves
  the branch onto a commit with such a suite asks as well;
- a commit status sets its context.

Each delivery carries GitHub's time, and a report that is older than what is
already known about its suite changes nothing, so deliveries that arrive out
of order leave each suite as the latest of them says. A check run that
passes says nothing about its suite until the suite finishes. Deliveries
about commits the branch is not on yet are kept, since they can arrive before
the push that moves the branch there.

A push says when it happened, and the latest push to the branch decides
where it is: pushes delivered out of order, or delivered again, leave the
branch where the last of them put it. A push GitHub gives no time for, the
first one heard of, or one in the same second as the last, moves the branch
only if it starts from the commit the branch is on and does not lead back
to one it has left; otherwise GitHub is asked. Check suites never move the
branch, but every check suite a push started is on the branch, so one at a
commit the branch has not been on means that push went undelivered.

GitHub is asked only for a starting point, since the relay keeps no history:
when the indicator starts or its settings change, once the relay connection
is up, and after waking. It is also asked when deliveries show that one went
missing: a push that cannot be placed after the last; a check run or check
suite on the branch at a commit it has not been on, or at one it has left
that a push from where it is started; the branch being deleted; and a
delivery that cannot be read. It is asked about cancellations as described
above. Once two answers running show the branch is
not on such a commit, deliveries about it stop asking, until the branch
moves. An answer that names a commit the branch has since moved away from
may be out of date, so the indicator keeps the newer commit and asks again
at the interval; if the next answer names the same commit, it is believed.
An answer asked for while the relay is connected is otherwise added to what
deliveries have said, by the same rule of GitHub's times, so an answer that
lags the deliveries does not undo them. Without the relay, each answer
replaces what is known about its commit.

Waking the computer, and **Refresh Now**, believe GitHub's answer instead:
it replaces what deliveries have said about the commit, and moves the
branch wherever GitHub says, even onto a commit deliveries showed it
leaving. That is how a workflow run that has been deleted stops counting,
since deleting a run is not delivered. In case that answer lags the
deliveries, GitHub is asked once more at the interval, and its answer is
added to what was delivered since.

Requests made because of deliveries never come more often than the interval
in the settings: one that comes less than the interval after the last
request is put off until the end of the interval.

When the connection ends, the indicator asks GitHub as soon as the interval
allows, since anything that changed while the connection was dying was never
delivered. Until the relay is back, the indicator asks GitHub at the
interval, as it does without a relay, and tries the relay again each time it
asks; the menu says when it will next ask. A request to GitHub that fails is
tried again at that interval too, and deliveries go on being followed in the
meantime. TCP keepalive notices a connection that died without closing
within about a minute and a half of quiet. Waking the computer starts a new
connection straight away, since the old one may have died while the computer
slept, and asks GitHub. **Refresh Now** asks GitHub, and tries the relay if
it is not connected.

A delivery of one of the four events that cannot be read, for example
because the webhook sends form data rather than JSON, is reported in the
menu, in place of the line that says the relay is connected, and so is a
frame from the relay that is not an HTTP request.

The indicator cannot notice a delivery that GitHub never sends to the relay,
unless a later delivery shows it. A check suite that finishes without its
finish being delivered stays running in the indicator until a wake, a new
connection, or **Refresh Now**.

The connection to the relay is neither encrypted nor authenticated, and the
indicator relies on the relay to check each delivery's signature, so the
relay should be reachable only over a network you trust.

## Building and running

Requires the Xcode command line tools, for `swiftc`. There is no Xcode
project. The menu bar indicator is `main.swift`. What it knows about a
commit's checks is in `checks.swift`, the requests to GitHub are in
`github.swift`, the relay connection and the deliveries it carries are in
`relay.swift`, and following the branch, and deciding when to ask GitHub,
are in `monitor.swift`. The plane is `flyby.swift`, and the hand-off to an
agent is `handoff.swift`. Running other programs is in `tool.swift`.

```sh
make            # builds ./ghbar
./ghbar         # runs an indicator, taking the lowest free instance number
make test       # tests following a branch and handing it off to an agent
```

The tests run a stand-in relay on the loopback interface, a stand-in for
GitHub that answers each request when the test says so, and a stand-in for
GitHub's HTTP API. A stand-in Codex executable checks that the hand-off
preserves its configured permissions, request, and session folder, and
queues the follow-up on the session it created. A test has no time limit:
it waits until what it is waiting for happens. With `TRACE_WAITS` set
in the environment, each wait prints its line number, so a test that never
finishes shows where it stopped.

The first indicator with nothing configured opens its settings window.

```
usage: ghbar [--instance N] [--repo OWNER/NAME] [--branch BRANCH]
             [--once] [--banner] [--hand-off] [--no-startup]

  --instance N   run as indicator N rather than the lowest free number
  --repo R       track this repository; in the menu bar, also remember it
                 for this indicator
  --branch B     track this branch; in the menu bar, also remember it for
                 this indicator
  --once         report the branch on standard output and exit, without
                 touching the menu bar; exits 0 when the branch is passing,
                 1 when it is failing, and 2 otherwise
  --banner       fly the plane and its banner straight away and quit when
                 it has landed, to see what it looks like
  --hand-off     hand the branch to the agent chosen in the settings
                 straight away, whatever state it is in, and exit, without
                 touching the menu bar
  --no-startup   leave the login item alone for this run
```

`--once` also works as a scriptable check on a branch:

```sh
ghbar --once --repo commontoolsinc/labs --branch main
```

## Installing

```sh
make install
"$HOME/.local/bin/ghbar" &
```

`make install` copies the binary to `~/.local/bin/ghbar`. Running that copy
once is what writes the launchd agent pointing at it, so the indicator comes
back at every login from then on.

```sh
make uninstall
```

This stops and removes every `local.ghbar.*` agent and deletes the installed
binary. Settings and lock files are left alone; they live in
`~/Library/Preferences` and `~/Library/Application Support/ghbar`.
