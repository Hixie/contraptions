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

Dragging the dashboard, by the handle along its top or by any empty part
of the runners board, tears it off: it becomes a window like any other,
which stays where it is put, in front of other windows or behind them,
until it is closed, the size it was, as the runners board is laid out for
that size. Like a torn-off palette, it then has a slim title bar with a
small button that closes it, as Escape does; the runners board puts its
heading, how many jobs run and wait, and its legend's button in the title
bar beside it, and the numbers and columns sit below it. Clicking the
indicator brings it to the front. Once closed, the dashboard opens below
the indicator again, as before. Nothing but a drag tears it off.

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
first, each named by its repository, its branch's open pull request if it
has one, and its workflow, as `loom PR #1234 · Tests`, and with, at the end
of its line, who opened the pull request, which opens it, or else its
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
**Show Runners**, which shows the runners board, described below, in place
of everything above the bottom row, and, while it shows, becomes **Show
Branches**, which shows the numbers and columns again; cibar remembers
which from one run to the next; **Settings…**, which opens the settings window, described below, or, with
Option held, sends a red plane and a green plane round; and **Quit cibar**.

## The runners board

The runners board shows what every runner is doing, and what is waiting
for one, as circles, one for each job queued or running on any branch,
coloured by run, so that the jobs of the same workflow for two pull
requests, or for two commits on one branch, have two colours. A circle
looks the same queued or running; where it is says which. Runs on the
board at the same time have colours of their own, while there are enough
colours to go round, and a run keeps its colour while it has jobs on the
board. There are twenty-four colours: twelve strong ones, which runs take
first, and twelve paler ones, each a lighter shade of one of the strong
ones, taken only when the strong ones are all in use.

The board is split into pools, each a set of runners that the same jobs can
run on. Each pool is a band across the board, headed at its top left by
its name and what its runners are, and at its top right by how many of its
jobs are queued and how many running, or, for a larger runner, how many of
how many it can run at once. Under the heading, the jobs queued for it are
on the left, and its runners on the right, in boxes. A queue is a heap of
balls, one for each queued job, in a space as tall as the pool's runners
and its heading, up to the pool's top, its balls drawn over the heading
and clipped to the pool's rounded box, so that however long it grows,
nothing below it moves; a ball leaves the clip when its job starts or
ends, so as not to be cut off on its way to its runner or off the board; its balls are as
small as lets them all fit, heaped as loosely as poured balls heap, down
to one point across, beyond which the heap rises above its space. A heap
that, poured, lies less flat than that, so that balls stick out of the
top, is made of balls half a point smaller, and smaller again, until they
all fit. Jobs just queued are set out, out of sight, in a pile of rows as
wide as the heap, the oldest at the bottom nearest the heap, centred two
heaps' widths to the left of the heap and two widths and three heights
above its floor. From there the pile falls onto a slope, at forty-five
degrees, that leads down to the heap's top left corner, slides down it,
and pours into the heap off the corner, a ball at a time, each out of
sight until it is half past the heap's left side. A heap wide and shallow
enough that the slope would cut through the pile has its pile raised just
clear of it, and a pile poured while another is still on its way in is
raised clear above it; as the balls tumble down the slope, they come in
roughly, not strictly, in the order they were queued. A wide, shallow
heap fills its left end first, balls coming in perching on the corner at
the top of the slope, and so is made of smaller balls than a heap poured
evenly would be. When the board is shown afresh, they drop in all at once
from just above. When a job leaves, for a runner or because it
was cancelled, the balls above it fall into its place.

Gravity and the balls knocking against each other, the sides and the
slope are worked out ahead of time, whenever a heap's jobs or space change, or its
balls are bigger than they may now be, from where each ball is and how it
is moving, and Core Animation moves the balls along the paths that gives.
The simulation (`heap.swift`) takes small steps, more of them the smaller
the balls, so that no ball moves much further than its own width in one
and so passes through another; each step moves every ball, then pushes
apart those that overlap, twice, finding them by a grid of squares a ball
across, from the floor up, the ball below giving way less than the one on
top of it, so that a deep heap bears its weight rather than squeezing its
bottom out sideways; a ball balanced right on top of another rolls off it,
away from the nearer side, and of two squeezed together side by side, one
rides up over the other. The heap's right side and floor go on for ever,
its left side is only as tall as the heap's space, and the slope runs up
and away to the left from the top of it, the corner between them a point
that balls roll over. It keeps the balls in the order of the squares they
are in, sorted again each step, so that neighbours are neighbours in
memory too. A ball is never flung upward: one pushed up by those under it
creeps up, at six of its widths a second at most. The heap is at rest once
no ball moves a quarter of a point, or a twelfth of its width if that is
less, between samples for a tenth of a second, and the simulation stops
then, or after five seconds, once the balls have grown, if all have come
into the heap by then, or else once they have, or at ten seconds at most.
Balls that grow,
as when most of a queue leaves at once, grow over half a second, as their circles
do, rather than at once, so that they part gently rather than burst apart.
A heap of a thousand balls shown afresh takes about fifteen milliseconds
to work out, three thousand between a tenth and a fifth of a second, and
six thousand, a point across, about half a second, all on the main thread;
pouring forty balls down the slope into a heap of a few hundred takes
about fifty milliseconds.

A runner, and the jobs that run on it, belong to the organization or user
that lists it, so runners of the same name of two owners are two runners.

- A runner whose name is one of its labels, or one of its labels, a dash
  and a word, as `ci-mac-01-3` is `ci-mac-01`, a dash and `3`, and that has
  another label, other than those for its system, to say what it is for, as
  `cf-mac` does, is on the machine the first label names. Each machine is a
  box, with a slot for each of
  its runners, named by that word, in order. A slot holds the circle of the
  job running on it; an offline runner's slot is red and crossed out.
  Machines that share a label other than their names, and every machine
  that shares one with them, are one pool, named after what their names
  start with, as `ci-mac`. Runners that an autoscaler makes for each job are
  usually named with more than one word after their labels, or have no
  other label, so they are not taken for machines. Should two jobs say they
  run on one runner, as deliveries that arrive out of order can, the second
  is shown beside the slots.
- Other runners, such as virtual machines that are made for a job and
  deleted after it, are pooled by their labels, other than those GitHub
  gives every self-hosted runner for its system, unless they are in
  GitHub's own runner group. A job that asks for a runner group rather than
  labels, as `runs-on: group: labs` does, has its runner pooled by that
  group, as `runner group labs`. Such a pool's box has a place for each job
  running on its runners, as described below. It says how many of its
  runners are idle when none has ever been busy while the board shows.
- Each of the organization's larger runners, such as
  `ubuntu-24.04-32-core`, which GitHub starts for each job up to a most it
  can run at once, is a pool of its own while it has jobs on the board,
  with a place for each job it can run at once, up to 64; one that can run
  more has places as described below. A job is on it when its runner, not
  a self-hosted runner cibar knows of, is named after it, as
  `ubuntu-24.04-32-core-1001427100` is, or asks for it by name.
- Runners in GitHub's own runner group, or named after it, as `GitHub
  Actions 1001438681` is, which GitHub starts for each job, are one pool,
  with any job running that GitHub has not yet said the runner of, as for
  a moment after it starts, unless it asked for a self-hosted runner, with a box for each set of labels that jobs ask for, as
  `ubuntu-latest`. How many of them may run at once depends on the
  organization's plan, which GitHub's API does not say.

So that running jobs never move while the board shows, every box of
runners other than a machine's has numbered places. A job takes the first empty place, or, with
none empty, a new one after the rest, and keeps its place while it stays.
A job that finishes leaves its place empty at once, for the next job to
take, even while its circle is still on its way off the board. A box never has fewer places than it has needed, and grows only
by lines. Boxes of places that share a pool are set out in as many
columns as fit at least eight places each, at the biggest circles, and
the longest of their names, so that smaller circles fit more to a line
rather than more columns, each box taking the next
column in turn, one under another, so that a box that grows moves only
the boxes under it. Pools and boxes stay where they are, empty if their
jobs have all gone, until keeping them would make the circles smaller or
leave a pool off the board, and those that come onto the board go after
them. The board starts afresh, in order, each time the dashboard opens or
changes size. These places are not counted as runners at the top of the board.

A queued job waits in the pool of the first online runner that has every
label it asks for, or, when all those runners are offline, of the first of
them. When no runner cibar knows of has those labels, the job waits in the
pool of runners with the labels it asks for, if there is one; else, if it
asks for a self-hosted runner, in a pool made for those labels, so that a
job that may never be taken can be seen; and else for one of GitHub's
runners.

Every circle that moves on its own moves only as forces move it, from
where it is, moving as it moves, so that none ever changes speed or
direction all at once (`motion.swift`). When a job starts running, its
circle is drawn to its runner's place, by a pull that comes on over a
quarter of a second, against gravity if it was falling, as one dropping
into its queue is, so that it goes on falling a moment and then curves
round to its place, where it comes to rest in about a second, the last of
it too slow to see; a job whose place moves goes there the same way. When
a job finishes, its circle, still in its run's colour, gains an outline
and a mark, a green ✓ if it passed, a red ✕ if it failed, or a grey – if
it was cancelled or skipped, swells for a moment while it slows to a stop,
and goes, over everything else, the dashboard's bottom row included. One
that passed is thrown, by a push lasting a tenth of a second, up, over and
off the board to the right, a little lower than it was. One that failed
leaves a red ring behind, and is thrown up and to the right, hard enough
to reach the right side of the board while still in the air, the push
ending as it hits; it bounces off the side at a fifth of the speed it hit
it at, falls to the line along the bottom of the board, and bounces along
it, each bounce back up at half the speed it came down at, going left at a
steady speed, as if the line were very bouncy and very slippery, and
sliding once its bounces have died away, until, past the line's left end,
nothing holds it up, and it falls. One that comes down between the line's
right end and the side falls through the gap there. One that was cancelled
or skipped rises like a puff of smoke, lifted by buoyancy and held back by
the air, so that it rises slowly at first and then ever faster, to a
steady speed, straight up and off the top of the board, growing and fading
as it goes. A job that goes for another reason, as when cibar loses its
connection, simply goes, and so do jobs on their way off when the board is
shown afresh, as when the dashboard opens. Core Animation moves the
circles along paths worked out ahead of time, without cibar drawing
anything between deliveries. While macOS is set to reduce motion, the
board changes without moving anything.

Across the top, how many jobs are running and queued, how many runners on
machines, which have slots of their own, are idle, and how many of those
are offline; under that, anything that is wrong, as under the dashboard's
numbers. A board with nothing on it says so. Each box of a machine says
how many of its runners are busy. The button at the top right opens a
legend beside the dashboard, to its right, or, with no room there, to its
left, which can be dragged anywhere, with a line for each run on the
board, in alphabetical order: its colour, its name, as in the Running
list, with its branch when it has no pull request, its commit, and how
many of its jobs show. The legend goes when the button is clicked again,
or when the board does, as when the lists show in its place or the
dashboard closes.

Pointing at a circle says its run's name, as in the Running list, with its
branch when it has no pull request, and its commit, as `@ 1a2b3c4`; then
which job
it is, and how long it has been running, and where, or how long it has
waited, and for what labels. Pointing at a slot names its runner and says
what it is doing, and pointing at an empty place says it is free.
Clicking a circle opens its job's page on GitHub.

The circles are as big as lets everything fit, so the board never scrolls;
pools that do not fit even with the smallest circles are left off, and
counted at the bottom. While the board shows, the circles only ever get
smaller, and the board only moves things when it runs out of room: a box
that needs another line pushes what is below it down, and smaller circles
move everything.
While cibar is not connected to the relay, it does not know which jobs are
queued or running, so the board shows none.

cibar reads which runners, and which larger runners, each owner has from
GitHub's API when it shows the board, as the dashboard opens or as
**Show Runners** is chosen, and, while it shows, after each read described
below, since no delivery says when a runner comes, goes, or loses its
connection. Runners that jobs run on that GitHub did not list
are taken from the jobs. A user, unlike an organization, has no runners
listed; runners that cannot be read are named under the numbers at the top,
with why.

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
Actions and pull requests, and, for the runners board to list runners that
are idle or offline, to the organizations' self-hosted runners.

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
in `state.swift`. The dashboard is `dashboard.swift`, its runners board
`runners.swift`, the heaps of its queues `heap.swift`, the paths of its
circles that move on their own `motion.swift`, and the plane is
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
restarts it; the Codex sessions it started go on. The agent's process type
is Interactive, so launchd does not limit the processor time or disk
bandwidth of cibar or of the commands it runs to start Codex sessions, as it
does for a job that names no process type.

```sh
make uninstall
```

This stops cibar and removes the agent and the installed binary. The
configuration and what cibar saved are left alone.