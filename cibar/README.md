# cibar

A macOS menu bar indicator that follows every GitHub Actions job in every
repository of one or more GitHub organizations, as the jobs run. It shows at
a glance how many jobs are running and how many last failed on default
branches. When a workflow on a repository's default branch, in a run that a
push started, turns red or green, a plane flies across the screen towing a
banner that says so. Whenever a job on a default branch fails, cibar starts
a Codex session to fix it. A pull request's runs start nothing: they are its
author's to look after.

cibar reads what is going on from GitHub's API when it starts, and when
asked to refresh, and from then on learns everything from GitHub's webhook
deliveries, which a relay passes on to it.

## What the indicator means

The indicator has up to four parts.

- **The symbol** sums up every repository's default branch. A green tick
  means no workflow on any default branch is red. A red cross means at least
  one is. A warning triangle means cibar is not connected to the relay, or
  cannot use its configuration, so it does not know.
- **A number in red**, after a red cross, is how many jobs last failed on
  default branches. Other branches are left out of it, as they are of the
  dashboard: their failures start nothing, and are their authors' to fix.
- **▶︎ and a number** is how many jobs are running on default branches, and,
  when the dashboard's **Include PRs** is ticked, on every other branch too.
  While cibar is not connected to the relay, the number is a dash.
- **⏸︎** says that no Codex sessions start, because they are paused or the
  settings have none.

Workflows are told apart by the files that define them, so two workflows of
the same name are two workflows, and a workflow renamed is the same
workflow, shown by its new name. A workflow on a branch is red when any of
its jobs last failed there, and green otherwise. "Last" means in the latest
run of the workflow on that branch that ran the job. A job that times out,
fails to start, or needs action counts as failing. A job that was cancelled
or skipped neither passes nor fails, so it leaves the job as it was:
concurrency groups cancel jobs all the time, and that says nothing about the
code. A run that fails without any of its jobs failing, for example because
its workflow file is invalid, counts as a failing job of its own, named "the
run", until a job of the run fails, which says why it failed, or a job of a
later run passes or fails, which shows that the workflow runs again. A run
that passes turns its workflow green on its branch, whatever earlier runs
failed, because a job that the passing run did not run is a job that has
been removed or renamed, or that no longer applies. From then on, a failure
from that run or an earlier one, delivered late, changes nothing.

A workflow turns red as soon as one of its jobs fails, while its other jobs
are still running. A job that its workflow allows to fail is no exception,
since nothing GitHub delivers says so: the workflow turns red when the job
fails, and green again when the run passes.

A delivery can change several jobs at once, for example when news of a run
lets cibar take in the jobs it set aside, as described in the section on the
relay. A workflow that such a delivery turns red and back again has not
turned.

A pull request from a fork runs on the fork's branch, which cibar names
after the fork's owner, as in `someone:main`, so that it is never taken for
the repository's own branch of the same name. A run whose fork has since
been deleted no longer says whose branch it ran on, so it does not count at
all.

## The dashboard

Clicking the indicator opens a dashboard, which stays up to date while it
shows. It is always the same size, as tall as a small screen allows, so that
it does not jump about as what it shows changes, and it stays where it
opened, however the indicator changes. It has the keyboard as soon as it
opens, without cibar becoming the active app, as Spotlight does, since
macOS does not make an app active just because it asks to be. Clicking the
indicator again, pressing Escape, or clicking anything else closes it at
once, and hands the keyboard back to the app that had it.

**Include PRs**, at the top of the Running column, says whether what runs on
branches other than default ones, such as pull requests', is shown and
counted in that column, in the Running and Queued numbers, and in the
indicator's ▶︎ number. What fails on them is never shown or counted: it
starts nothing, and is its author's to fix. cibar follows those branches
either way, so ticking it shows them at once. cibar remembers it from one
run to the next.

Across the top, four big numbers, whose digits turn like an odometer's
wheels as they change while the dashboard shows, each through every step
from where it was to where it is to be, so that the last turns fastest and
each before it a tenth as fast, blurring and fading as they turn, and a
digit that comes or goes turning from or to a blank as it would from or to
any other digit; the dashboard opens with them as they are:

- **Default Branches**: how many of those listed below are green.
- **Failing**: how many jobs last failed on default branches, and in how
  many workflows.
- **Running**: how many jobs are running, and in how many workflows.
- **Queued**: how many jobs are waiting for a runner.

Below them, two columns, each half the width. On the left, **Failing on
Default Branches**: every workflow on a default branch that is red, with its
failing jobs, the branch going without saying, each of which opens its page
on GitHub, and beside each, what cibar did about it: how long ago it sent
the job to Codex, which the system keeps up to date, opening the session in
the Codex app; or, in brackets, why it did not, such as "Codex paused", or
"cibar missed it" for a job that failed while cibar was not listening; and,
when a later run of the job is under way, which will replace its failure,
"running again", which opens that run's job. Then
**Default Branches**: each repository whose default branch is red, or that
a workflow finished on in the last day, the busiest first:
the one most workflows have finished on, each counting half as much for
every five days since it finished, which changes little from one finish to
the next, each opening the branch's runs on GitHub. Under them, **Send
default-branch failures to Codex**, which says whether each job that fails
on a default branch starts a Codex session, and turns that on or off; cibar
remembers it from one run to the next.

On the right, **Running**: every run with jobs queued or running, the newest
first, each with, at the end of its line, the number of its branch's open
pull request, which opens the pull request, and who opened it, or else its
branch. cibar learns of open pull requests from GitHub's API and from
deliveries about them. Beneath each run, in red, the jobs of it that have
already failed, each opening its page; then its jobs still to finish, in the
order GitHub made them, with a play sign beside each one running and a clock
beside each one queued, each opening its job's page: the first named, in as
many columns as names that long fit, eight rows of them, and the rest, if
there are more than one, as their signs alone, wrapped, so that every job is
there, each naming its job while pointed at. The run's name opens the run.
Each column scrolls when it has more than fits. Wherever a name has a full
commit hash in it, the dashboard shows the hash's first seven digits, as git
does.

Under the numbers, anything that is wrong: why the settings cannot be used,
why cibar is not connected to the relay, why a delivery could not be read,
or why cibar could not read all of GitHub's API.

Along the bottom: whether cibar is connected to the relay. While it is, a
bar for each delivery of the last ten seconds shows how long after GitHub
made it the delivery arrived, where it arrived, now at the right, from none
at the bottom to eight seconds or more at the top, coloured from grey
through orange, at five seconds, to red, at eight, by how tall it is. Beside
it, the latest delivery's latency, in orange when it was more than five
seconds, fading away by the time its bar has scrolled off the chart. GitHub or the relay may be delivering
slowly, a delivery may have waited while the computer slept, or this
computer's clock may be wrong. Core Animation scrolls the bars and fades the
latency, as smoothly as the screen can show, without cibar drawing anything
between deliveries. Then **Refresh**, described below, which, while cibar
reads, gives way to a bar of how many of how many repositories it has read;
**Settings…**, which opens the settings window, described below, or, with
Option held, sends a red plane and a green plane round; and **Quit cibar**.

## Reading from GitHub's API

Whenever a new connection to the relay first delivers something, as one does
soon after cibar starts, after its settings are saved, and after the
connection is lost, and when **Refresh** is chosen, cibar reads what is
going on from GitHub's API, and starts afresh from that, forgetting what no
longer is. So a workflow that will never run again, because its file was
deleted or it was disabled, or because its branch's pull request has closed,
stops counting as red, and a job whose end was missed while cibar was not
connected stops counting as running. A relay that accepts connections but
delivers nothing causes no reads. Asked to read again while reading, cibar
reads again once it is done.

It reads the repositories of each owner in the settings, or, with none, of
each owner of a repository it knows: all those of an organization the token
can see, all those of the token's own user, and the public ones of any other
user. For each that is not archived, and has been pushed to in the last week
or has a red workflow, it reads the repository's workflows and open pull
requests, the latest hundred runs on every branch, and the latest hundred on
the default branch, which reach further back for workflows that run less
often. Of those, it keeps the runs of active workflows, on the default
branch or the branch of an open pull request, or on any branch while they
are under way. Then it reads the jobs of each run that is under way, and of
the latest run of each workflow on each branch to pass or fail, when it
failed. It reads four repositories at a time, starting another as each is
done, and six runs' jobs at a time. That is four or five requests for each
repository, and one for each such run. It keeps GitHub's answers in
`~/Library/Caches/cibar/github`, so that asking again is a conditional
request, which GitHub does not count against the token when the answer has
not changed.

What cibar knew is kept wherever the read does not contradict it: for every
repository not read, including one that could not be, and, in a repository
read, for each active workflow on the default branch or an open pull
request's branch that the read saw no run of, as it may not of one that runs
seldom. Branches cibar had forgotten stay forgotten. Anything that could not
be read is named under the dashboard's numbers, with why. When GitHub says
the token has no requests left, cibar stops reading, and says until when.

Deliveries that arrive while cibar reads, and in the minute before, in case
GitHub's answers lag behind, are taken in as usual, and taken in again once
what it read has been, since they may be newer. What it reads starts no
Codex sessions and flies no planes, and nor does what it takes in again,
except a failure of a run it had not heard of before the read, which starts
the session the failure would have.

cibar asks the API with a token, which it gets from the login shell, as a
terminal would, the first time it reads, and again after a read fails, in
case it has changed: `GH_TOKEN`, then `GITHUB_TOKEN`, then what `gh auth
token` prints. The dashboard says while it waits for the shell. Without a
token, cibar reads nothing. The token needs read access to the repositories'
Actions and pull requests.

## The planes

When a workflow on a repository's default branch turns red or green in a run
that a push started, a plane flies out from behind the indicator towing a
banner, for example "labs CI is red". The plane's trim and banner are red
when the workflow turned red, and British racing green when it turned green.
A workflow that cibar has not seen before turning red is news, so a plane
flies. One it has not seen before turning green is not, so none does.

The plane dives away to the left, does a few aerobatics chosen afresh each
time, turns round, and climbs back in behind the indicator. When several
workflows turn at once, several planes fly. Each sets off once the banner of
the one before it has cleared the indicator, and each flies a path of its
own, so they follow each other out rather than overlap. Nothing about a
plane can be clicked; the pointer goes straight through it.

Running `cibar --banner` sends a red plane and a green plane round straight
away, and quits once both have landed. It has no dashboard, and does not
connect to the relay, so it can run beside the copy that does.

## Codex sessions

Whenever a job fails on a repository's default branch, cibar starts a Codex
session to fix it, unless the settings have no Codex sessions or they are
paused. It starts one for each job, as soon as the job's failure is
delivered, so a workflow whose shards fail one after another starts a
session for each, and a job its workflow allows to fail starts one too. A
run that fails without any of its jobs failing starts one for the run. A
run's failure can be delivered before its jobs' are, so when any of its jobs
is unfinished then, the run waits for them: if one fails, that job's session
is the run's, and if all pass, the run starts one of its own. There is no
limit on how many run at once; the sessions are left to notice each other
and not do the same work twice. A failure that does not count, because a
later run has already passed the job, or because GitHub delivered it again,
starts none.

Only runs started by a push, a pull request, a merge queue, a schedule, a
person starting the workflow by hand, another workflow, or a repository
dispatch start sessions, and only when the run did not come from a fork.
Runs started by other events, such as `pull_request_target` or
`issue_comment`, run for whoever opened the pull request or wrote the
comment; and a fork's owner chose its branch, its workflow file and its job
names. Those would all go into the request.

The Codex app runs the session as it runs its own, with the tools it gives
them, such as for naming a session, but without coming to the front. A
session run through Codex's program interface, `codex app-server`, from
the Codex command-line tool that ships inside the app, is not one the app
runs, and has none of its tools, even when the app lists it. So cibar makes
the session through `codex app-server`, in the folder the settings give,
naming it after what it is for, which saves it, since a session in which
nothing has happened is not saved; queues the request in it with `codex
queue`, as the server the session was made in would run it at once; and
opens `codex://threads/wake/ID`, without bringing the app to the front,
which has the app take up what is queued in the session. The app runs it
with its own sandbox and approval settings. Starting a session takes a few
seconds, which cibar spends off its main thread; should cibar quit
meanwhile, the session is lost, though the app may list it, named, with
nothing in it.

The request names the job that failed, its workflow, branch and repository,
and the job's page, followed by a report: the job again; every job failing
on the branch, its page, and the Codex session cibar already started for
it, if any; the jobs of the same run that had failed before GitHub made
this one, which it may need, as GitHub makes a job that needs others only
once they have finished, but the jobs of a matrix together, however long
they then wait for a runner; the run of the same workflow that passed the
same commit on the branch, if one did, in which case something other than
the code may have failed, such as a network request, or a test that passes
only some of the time; the commit; and what started the run. The session
can tell from these whether the failure is another's, already being fixed,
or one to track down as a flake. The report leaves out the run's title,
which for a pull request is whatever its author wrote.
The configuration can change the request.

## The relay

GitHub can only deliver a webhook to an address on the public internet, so
deliveries go to a relay, which passes each one on to every program
connected to it. cibar connects to the relay over plain TCP, and expects
each delivery as a frame: the length of an HTTP request, as a 32-bit big-
endian integer, followed by the request exactly as the relay received it.
The webhook that feeds the relay must cover the organization, use the
`application/json` content type, and send at least the "Workflow jobs" and
"Workflow runs" events. "Branch or tag deletion" events let cibar forget a
branch once it is deleted, and "Pull requests" events a fork's branch once
its pull request closes, and say whose each pull request is. Runs on a
forgotten branch that GitHub had made by then no longer count, even after
cibar restarts, so that a run still going when its branch was deleted, as
when a pull request is merged, does not bring the branch back. A run that
someone runs again afterwards counts from when it started again, so it does
bring the branch back.

A delivery about a run that does not say which file defines its workflow
cannot be read, and the dashboard says so.

A delivery about a job does not say what started its run, or which
repository's branch it ran on, so cibar sets each job aside until a delivery
about its run arrives. GitHub delivers news of a run when it is queued, when
it starts, and when it completes, and the first of those arrives within a
few deliveries of the first about its jobs. A run that was already under way
when cibar connected is read from GitHub's API, as described above, so its
jobs need not wait for news of it.

The connection to the relay is neither encrypted nor authenticated, and
cibar relies on the relay to check each delivery's signature, so the relay
should be reachable only over a network you trust.

The relay keeps no history, so cibar misses whatever happens while it is not
connected. When the connection ends, cibar forgets which jobs are running,
since it would never hear that they finished, and tries again after one
second, then two, four, and so on, up to 64 seconds; once a connection has
lasted longer than the next wait would be, the waits start again from one
second. Nothing tells cibar when the relay is back, so it has to try. Once
it is connected again, it reads what it missed from GitHub's API, as
described above.

What cibar knows about how jobs last finished is kept, and saved to
`~/Library/Application Support/cibar/state.json`, so it survives cibar
quitting. A file there that cannot be read is moved aside, with
`.unreadable` added to its name, and cibar starts afresh; an entry in it
that cannot be read, as one a later version of cibar wrote may not be, is
left out. What cibar did about each job that failed on a default branch,
and when, which thread each session it started is, or why it started none,
is saved beside it, in `sessions.json`, until the job next passes; an entry
there that cannot be read is left out too.

Waking the computer connects again straight away if cibar is not connected.
TCP keepalive notices a connection that died without closing within about a
minute and a half of quiet.

Deliveries can arrive out of order. A delivery about a job that is older
than one already heard, or about an earlier attempt at a run that has been
run again, changes nothing. A delivery that says a job failed in an earlier
run than the one that last finished it changes nothing either.

## Settings

The settings window sets the relay, the repository owners to follow, whether
Codex sessions start, the folder each session starts in, and the request,
which can span several lines. In it, `{repository}`, `{branch}`,
`{workflow}`, `{job}`, `{job_url}`, `{event}`, `{sha}`, `{run_url}`,
`{checks_url}` and `{report}` are filled in. An empty request starts no
session. Until it is set, the request names the job that failed and gives
the report. **Save** checks the settings, writes them to the configuration
file, and, when the relay or the owners changed, opens a new connection to
the relay, which reads from GitHub's API again; settings that are wrong are
not saved, and the window says why. The window opens by itself when there is
no configuration file.

The configuration file is `~/.config/cibar/config.ini`, or the file given
with `--config PATH`. See [config.example.ini](config.example.ini). It can
be edited by hand, and cibar reads it again when it starts, but saving the
settings window writes it afresh, with cibar's own comments. In values, `\n`
is a line break and `\\` a backslash. cibar refuses a file that sets a key
it does not know or sets one twice, so that a misspelling cannot go
unnoticed; the dashboard says why. Leaving an owner out of `owners` forgets
that owner's workflows, since no delivery about them would ever change them
again.

## Building and running

Requires the Xcode command line tools, for `swiftc`. There is no Xcode
project. The menu bar app is `main.swift`, and its indicator is
`indicator.swift`, and its settings window is `settingswindow.swift`.
Reading and writing the configuration is in `configuration.swift`, keeping
the connection to the relay open is in `connection.swift`, reading
deliveries from it is in `relay.swift`, following jobs and runs is in
`watch.swift`, deciding what a failure or a workflow turning calls for and
running Codex sessions is in `codex.swift`, and saving what cibar knows is
in `state.swift`. The dashboard is `dashboard.swift`, and the plane is
`flyby.swift`.

```sh
make            # builds ./cibar
./cibar         # runs the menu bar indicator
make test       # tests everything but the menu bar, its windows, and drawing
```

The tests run a stand-in relay on the loopback interface and a stand-in
Codex tool. A test has no time limit: it waits until what it is waiting for
happens. With `TRACE_WAITS` set in the environment, each wait prints its
line number, so a test that never finishes shows where it stopped.

Only one copy of cibar follows the relay at a time, so that each failure
starts one session, not one for each copy. A second copy says so in the log
and waits for the first to quit before it starts, rather than exiting, which
would make launchd start it again and again.

cibar logs each connection to the relay and each time it ends, each workflow
that turns, each delivery it cannot read, each Codex session it starts or
does not start and why, and each plane it flies, with where on the screen it
starts from. The lines go to standard error and to the system log:

```sh
/usr/bin/log show --last 1d --predicate 'subsystem == "local.cibar"'
```

```
usage: cibar [--config PATH] [--banner]
```

## Installing as a login item

```sh
make install
```

This copies the binary to `~/.local/bin/cibar`, generates a launchd user
agent at `~/Library/LaunchAgents/local.cibar.plist` from the template in
this directory, and starts it. The agent starts cibar at login and restarts
it if it crashes. Running `make install` again rebuilds, reinstalls, and
restarts it; the Codex sessions it started go on.

```sh
make uninstall
```

This stops cibar and removes the agent and the installed binary. The
configuration and what cibar saved are left alone.