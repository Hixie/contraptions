# ghbar

A macOS menu bar indicator that shows whether the checks GitHub runs on one
branch of one repository are passing. The indicator watches the head commit of
the branch, so it follows the branch as new commits land on it.

## What the indicator means

The shape changes along with the color, so the indicator still reads when red
and green do not.

- **Green tick**: every check that reached a verdict passed.
- **Red cross in an octagon**: at least one check failed. A check that was
  cancelled, timed out, or asked for action counts as a failure, which is also
  how GitHub's own summary counts them.
- **Orange clock**: no check has failed, and at least one is still queued or
  running.
- **Gray dash**: GitHub reported no checks on the commit, or reported only
  checks that were skipped or neutral.
- **Gray question mark**: the state is not known, because the last request to
  GitHub did not work. The menu says why.
- **Gray gear**: no repository has been chosen yet.

Next to the indicator is the repository's name, so several indicators can be
told apart at a glance. The name can be replaced with one of your own, or
turned off, in the settings.

## The menu

Clicking the indicator opens a menu that names the repository and branch, sums
up the checks, and names the head commit. Checks that GitHub counted but would
not name are counted separately rather than passed over. Below that come the checks that are
failing or still running, each of which opens its own page on GitHub when
clicked. Everything else is in the **All Checks** submenu, which lists every
check on the commit, failures first. **Open Checks on GitHub** opens the head
commit's checks page.

The line above the last separator names the indicator's number, when it last
heard from GitHub, and how much of the hourly request allowance is left.

The menu keeps changing while it is open, so a check that finishes while you
are reading moves as it finishes.

## Settings

**Settings…** opens a window with the repository, the branch, the name to draw
in the menu bar, and how often to ask GitHub. The repository can be written as
`owner/name`, or pasted in any of the forms GitHub hands out: the address of
the repository, the address of a branch, or the SSH remote. Pasting the address
of a branch fills in the branch as well.

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

Every launch writes `~/Library/LaunchAgents/local.ghbar.N.plist`, a launchd
agent that starts that indicator again at the next login. Several indicators
mean several agents, one per number, and every one of them starts at login.
The agent points at wherever the program was run from, so running the copy in
`~/.local/bin` is what registers that copy.

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

GitHub does not offer the **Checks** permission to fine-grained personal
access tokens at all; only a GitHub App can be granted it. Searching for it in
the token editor finds nothing, because there is nothing to find. Reading a
private repository's check runs through the REST API is therefore closed to
the kind of token GitHub now recommends.

What is not closed is GitHub's own verdict. Ask through the GraphQL API and it
answers with the state it has computed across every check on the commit,
whether or not the token may look at those checks one by one. That is the
answer this app wants, so whenever it has a token it asks that way, and a
private repository works with an ordinary fine-grained token.

What such a token loses is the list, not the color. The indicator is right,
the menu reports the verdict and how many checks it was taken over, and it
says how many of them it could not name: "Passing: 41 not listed by this
token". Read-only **Contents** is what gives the line naming the head commit.
A classic token with the `repo` scope can name every check.

Adding a permission to a token an organization has already approved can put
that token back in front of the organization's owners, so a permission that
has been added but not yet approved reads exactly like one that was never
added.

## Requests and the rate limit

With a token, each poll is one GraphQL query, which brings back the verdict,
the head commit, and as many of the checks as the token may see. It costs one
point of an hourly five thousand, so a poll a minute spends sixty.

Without a token GraphQL is closed, since it serves no anonymous callers, and
the REST endpoints are asked instead: the branch's combined commit status and
the check runs on the head commit each poll, and the commit message once per
commit. Both polled requests carry the ETag from the previous answer, and
GitHub charges nothing for an answer of "nothing has changed", so an idle
branch costs almost none of the sixty requests an hour an anonymous caller
gets.

## Building and running

Requires the Xcode command line tools, for `swiftc`. There is no Xcode project;
the whole app is one Swift file.

```sh
make            # builds ./ghbar
./ghbar         # runs an indicator, taking the lowest free instance number
```

The first indicator with nothing configured opens its settings window.

```
usage: ghbar [--instance N] [--repo OWNER/NAME] [--branch BRANCH]
             [--once] [--no-startup]

  --instance N   run as indicator N rather than the lowest free number
  --repo R       track this repository, and remember it for this indicator
  --branch B     track this branch, and remember it for this indicator
  --once         report the branch on standard output and exit, without
                 touching the menu bar; exits 0 when the branch is passing,
                 1 when it is failing, and 2 otherwise
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
