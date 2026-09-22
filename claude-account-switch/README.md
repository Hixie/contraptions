# claude-account-switch

## TL;DR

Run the commands from this directory.

The first time you switch:

1. Signed in to the account whose sidebar has your sessions, run
   `./account-switch.sh record`.
2. In the app, sign out, sign in to another account, and wait for the
   app to finish opening.  Do not start any work.
3. Quit the app completely (⌘Q).
4. Run `./account-switch.sh move`.
5. Open the app.
6. File any sessions `move` lists into sidebar groups, or ask Claude to.

Every time after that, to any account:

1. In the app, sign out, sign in to another account, and wait for the
   app to finish opening.  Do not start any work.
2. Quit the app completely (⌘Q).
3. Run `./account-switch.sh move`.
4. Open the app.
5. File any sessions `move` lists into sidebar groups, or ask Claude to.

If you ever switched with the old `link` command, do this once first:

1. Quit the app completely (⌘Q).
2. Run `./account-switch.sh move`.
3. Run `./account-switch.sh recover --apply`.
4. Open the app.

## About

Carries the Claude desktop app's session list from one Claude account to
another, so that signing out of one account and into a second one does not
leave you looking at an empty sidebar.

Everything this script touches is a local file under your home directory.
It moves one on-disk index between accounts you are already entitled
to use.  It has nothing to do with billing, rate limits, or how either
account is authenticated.

## The problem it solves

The Claude desktop app keeps two quite different things on disk, and only
one of them is tied to the account you are signed in as.

The conversation history is not tied to your account at all.  Every turn
of every session is one line of JSON in a file named after the session:

```
~/.claude/projects/<working-directory-name>/<session-id>.jsonl
```

Nothing in those paths or those files identifies an account.  Signing out
and back in as somebody else does not touch them.

The list of sessions the app shows you in the sidebar is tied to your
account.  The app writes one file per session, holding the title, the
star, the working directory, the model, the effort level, the permission
mode, any linked pull requests and the set of enabled tools, into a
directory named after your account identifier and your organization
identifier:

```
~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/
~/Library/Application Support/Claude/local-agent-mode-sessions/<account>/<org>/
```

Signing in as a second account makes the app work out a different
directory name, find nothing there, and show you an empty sidebar.  The
transcripts are all still on disk; what is missing is the index that
points at them.

Neither identifier appears anywhere inside the session files themselves.
This was checked by searching every file in a directory of 1084 of them,
and neither the account identifier nor the organization identifier
occurred in any of them.  Only the directory name carries that
information.  So the whole list can be handed to the second account by
moving the first account's directory to the second account's directory
name.  Within one disk, that move is a rename, however large the list is.

The directory has to be moved, not linked.  The app opens its session
directory with `O_DIRECTORY | O_NOFOLLOW`, which refuses a symbolic link,
so when the directory is a link every save fails:

```
Failed to save session local_<id>: ENOTDIR: not a directory, open '.../claude-code-sessions/<account>/<org>'
```

The app still reads the list through a link, so the sidebar looks right,
but every change stays in memory.  An earlier version of this script
linked the directories, and every session started under the second
account was gone from the sidebar once the app quit.  The transcripts
were intact.  `recover`, described below, rebuilds the lost entries.

## Running it

```bash
./account-switch.sh status    # who is recorded, who is signed in
./account-switch.sh record    # once, signed in as the account holding the list
./account-switch.sh move      # after each switch to another account
./account-switch.sh groups    # what each account has filed in sidebar groups
```

The whole procedure:

1. Run `record` once, while signed in as the account whose sidebar has
   your sessions.  It writes the account and organization identifiers to
   `~/.claude-account-switch-state`, which is the only state the script
   keeps.  That file stays in your home directory and is not part of this
   repository, because it names your accounts.
2. Sign out in the app, sign in as another account, and let the app
   finish opening.  It needs to have opened at least once as that
   account, because `move` finds the organization identifier by looking
   at the directory the app creates when it signs in.  Do not start work
   yet: the sidebar is empty until `move` has run.  (If the old `link`
   command linked this account's directory, the sidebar shows the list
   but nothing you do is saved; see the last list in the TL;DR.)
3. Quit the app completely.  `move` refuses to run while the app is
   running, because it moves files the app writes to as it goes.
4. Run `move`.  It moves the list into the signed-in account's directory
   and records that account as the one holding the list.
5. Open the app again.  The full session list is there.

To switch again, to this account or any other, repeat steps 2 to 5.
`record` is not needed again.  The script records which account holds
the list, so this works with any number of accounts.

`status` is safe to run at any time and changes nothing.  It prints the
recorded account and the signed-in account side by side, with the entry
count for each directory, and lists every symbolic link left by the
earlier version of this script.  Running it is the quickest way to see whether a
switch has actually taken effect.

`move` is idempotent.  Run a second time it reports that the list is
already with the signed-in account and does nothing else.

## Which file says who you are signed in as

`~/.claude.json` is the wrong place to look, and this is the single most
misleading thing about the layout.

Its `oauthAccount` block looks authoritative — it holds an account
identifier, an organization identifier, an email address and a display
name — but it is a cached copy of the account profile, rewritten only
when something successfully fetches the profile afresh.  After a switch
it goes on naming the previous account, for a long time.  It was observed
still naming the first account while the app had been signed in as the
second account for over half an hour, with other parts of that same file
being rewritten throughout, so the file as a whole is live; it is the
`oauthAccount` block alone that lags.  Taking the account from that block
after a switch therefore hands back the account you have just left, and
the failure is silent: a comparison against it reports that nothing has
changed, rather than reporting an error.

Two places do track the account that is actually signed in.  The app's own
configuration file,
`~/Library/Application Support/Claude/config.json`, holds it under the key
`lastKnownAccountUuid`.  The app also creates the `<account>/<org>`
directory at the moment it signs in, so the newest such directory under an
account is the organization now in use.  The script uses both of these,
and reads `~/.claude.json` only to put an email address next to an
identifier when it prints one.  When that cached snapshot is about some
other account, it prints `unknown` rather than a name that would be wrong.

## What `move` does with files the signed-in account already wrote

By the time `move` runs, the signed-in account has normally written a file
or two of its own, such as `scheduled-tasks.json`, which the app creates
at startup.  If it has been used on this machine before, it has a whole
history of its own.  Each of those entries is moved into the list being
carried, and then the carried directory takes the signed-in account's
directory name.  Session files are named after their session, so two
accounts' sessions never collide.  An entry whose name is already taken
in the carried list is set aside instead of being written over, and
`move` names each entry it sets aside.  A directory that both have, such
as the `agent/` directory of agent-mode sessions, is merged the same way,
one entry at a time.

Besides one file per session, each account keeps a few files of its own,
and these can collide when both accounts have been used:

- `archived-sessions.idx` lists the sessions that are archived.  The
  carried list keeps its own, and the signed-in account's is set aside.
  Nothing is lost: each session file records whether the session is
  archived, and the app decides from that.  The app uses the index only
  to load archived sessions after the others, and rewrites it from the
  session files every time it loads them.
- `scheduled-tasks.json` holds the account's scheduled tasks.  It is not
  merged.  The carried list keeps its own tasks, and any the signed-in
  account had are only in the set-aside copy.
- Anything else `move` names is set aside the same way, and the carried
  copy is the one that is kept.

The directory it is set aside in is made fresh for each run, named after
the signed-in account's directory with `.superseded.` and six random
characters on the end:

```
.../claude-code-sessions/<account>/<org>.superseded.a4Kf2p/
```

A fresh directory per run is what makes switching between the same two
accounts more than once safe.  Under a single fixed name, the second
switch would write over what the first one set aside.

Nothing is deleted except the symbolic links described next.  Once the
app has come back up and the session list looks right, the set-aside
directories can be removed by hand.

If the signed-in account's directory turns out not to be empty after the
moves, `move` says so and leaves it alone rather than replacing it.  When
anything could not be moved, `move` says so, exits with a failure status,
and leaves the recorded account as it was, so running `move` again
carries on where it stopped.

`move` also undoes the layout the earlier version of this script left
behind.  That version made symbolic links from other accounts'
directories to the list.  A link like that holds no data, and `move`
removes every one it finds, in any account's directory, before anything
else.  It does so even when the list is already with the signed-in
account.  If the recorded directory is itself such a link, `move` carries
the directory the link leads to.  A link at the signed-in account's
directory that leads anywhere else is left alone: `move` checks for one
in both trees before changing anything, and stops if it finds one.
`status` lists every link it finds.

`test-move.sh` runs `move` through these cases in a scratch home
directory, with the check for a running app stubbed out.

## Sidebar groups

The Code tab's sidebar groups are not in the session directory, so `move`
does not carry them.  The claude.ai page that draws the sidebar keeps
them in its own local storage, under the key `dframe-store`, as a map
from `<account>/<org>` to that account's groups and the sessions filed
in each.  So each account keeps its own groups.  Switching back to an
account brings its groups back, with the sessions it had filed in them,
because sessions are filed by identifier and the identifiers do not
change when the list moves.

The page also keeps a record of which session is filed in which group,
and the app copies that record into its `claude_desktop_config.json`,
under `preferences.epitaxyPrefs["dframe-group-scopes"]`.  A group with
nothing filed in it is not in that record.

`groups` reads that record, changing nothing, and lists what each
account has filed in groups.  If the record cannot be read, or is not in
the form expected, it says so and fails.  A config with no record at all
is taken to mean that nothing has been filed in a group on this machine.

After moving the list, `move` lists the sessions the previous account had
filed in groups that the signed-in account has not filed the same way,
leaving out sessions that no longer exist.  If it cannot read the record,
it says so and still succeeds, because the list has already moved.

A script cannot file sessions in groups.  The tools that create groups
and file sessions in them belong to the running app, which gives them
only to the Claude sessions it hosts, and `move` runs while the app is
quit.  So file the listed sessions in the sidebar yourself, making any
group that is missing, or ask Claude in a Code session in the app to do
it.  Those tools file Code sessions; any other kind of item in the list
has to be filed by hand.  In the default permission mode, Claude asks
before moving each session.

`move` does not copy groups between accounts.  The page syncs
`dframe-store` to the account's settings on claude.ai, and merges the
server's copy back into it, so groups written into local storage from
outside could be replaced.  Writing to that storage, a LevelDB database,
from outside also risks the app treating it as corrupt and discarding
all of it, including drafts and settings.

## Recovering sessions the app failed to save

`recover` rebuilds the sidebar entries of sessions whose saves failed,
which is what happened to every session started while the directories
were linked.  Run it after `move`, with the app quit; it refuses to run
until the list is in place with the signed-in account.

```bash
./account-switch.sh recover            # report what would be written
./account-switch.sh recover --verify   # check the method, as below
./account-switch.sh recover --apply    # write it, with the app quit
```

It works from three records that were written regardless:

- The app's own log, `~/Library/Logs/Claude/main*.log`.  It names every
  session whose save failed, and records the changes the app made in
  memory: when a session started and in which directory, which
  transcript it was using, whether it was a fork or a spawned task and
  of which session, directory changes, stars, archiving, deletion,
  permission mode changes and whether the app refused them, which kind
  of change last set the title, and which pull requests were linked to
  the session or dismissed from it.
- The transcripts in `~/.claude/projects`, which hold each session's
  titles, model, effort, permission mode, the time of its last turn, and
  the addresses of the pull requests it mentioned.
- GitHub, which `recover` asks with the `gh` command for each linked
  pull request's branch, base branch and state.  If `gh` cannot read
  one, `recover` reports it and leaves that link out.

The app checks its session directory once per launch, so every change
from the launch that made the first failed save onwards was lost.
`recover` replays each session's logged changes from that launch up to
the session's last failed save.  It then brings the session's model,
effort and permission mode up to date from its transcript, where the
transcript is newer than the logged change.  It writes a new file for a
session that has none.  For a session whose file is out of date, it
changes only those fields, its time of last activity, and its linked
pull requests.  A field the app changed again and saved after the last
failed save keeps its saved value, and a session deleted since then is
left deleted.  `--apply` first copies each file it is about to replace
into a new directory next to the session directory, named
`<org>.before-recover.` and eight random characters, and says where.

Some fields are not in the log or the transcripts: the number of turns
completed, the branches the session wrote to, its earlier titles,
permissions granted during the session, and suggested background
tasks.  A session `recover` creates starts these empty, as a new session
does.  It takes the account's connector settings from the most recently
active session.

The log does not last.  The app keeps five log files of about 10 MB
each, which on the machine this was written for held three weeks.
`recover` warns when the log no longer reaches back to the launch that
made the first failed save, because the changes before the oldest line
are gone.

`--verify` checks the method on sessions whose files were saved, in two
ways.  First, it rebuilds every such session whose whole life is in the
log as if none of it had been saved, and compares each field with the
file.  On the machine this was written for, it rebuilt 261 sessions.
Every one matched its file in session and transcript identifiers,
earlier transcript identifiers, working directory, creation time, model,
effort, archived and starred state, title, title source, fork and spawn
parents, and permission mode.  The time of last activity matched in all
but one session, which was in use while the check ran.  The time a
session was last focused differed in 5 sessions, because the log does
not record every change of focus; that field only orders the "recently
viewed" list.  Linked pull requests are not compared, because that log
only records links from September 17 onwards.  Second, it replays each
saved session's logged history onto its own file, which should change
nothing.  Over 302 sessions, the only changes were to the time of last
activity of 4 sessions, each by under a minute, where the transcript's
last entry is later than the time the app recorded.

On that machine, `recover` found 81 sessions that had no file, 18 of
them active and 63 archived, and 23 older sessions to update, mostly to
archive or rename them.  It linked 43 of the recovered sessions to the
pull requests the log recorded for them.  Applied to a copy of the
session directory and then run again on the copy, it found nothing more
to write.

`test-recover.py` runs `recover` against a made-up log, transcripts and
session directory, with a stand-in for `gh`.  It covers each of the
behaviors above, including a refused permission mode, a transcript that
ends with a notice from the command line tool rather than the model, a
session deleted after its last failed save, and a log that no longer
reaches back far enough.

## What it deliberately does not touch

The transcripts, because they are not account-scoped and were never at
risk.  `recover` only reads them.

`skills-plugin`, which sits inside `local-agent-mode-sessions` alongside
the account directories.  It holds a cache the app downloads again, so
there is nothing to carry across.  It is also nested the other way round,
as `<org>/<account>`, which is why the script builds every path from an
account and an organization explicitly and never searches these
directories for things that look like account identifiers.

The `claude` command line tool, which keeps its own credentials in the
login keychain under `Claude Code-credentials`, separately from the
desktop app's encrypted token in `config.json`.  Signing the command line
tool in or out does not disturb the app, and this script does not read or
write either one.

## What was verified, and what was not

Verified on a real switch between two accounts:

- The first account's session directory survives a sign-out untouched.
  All 1084 files were still there afterwards.
- The app refuses to write through a symbolic link at its session
  directory.  Its log shows the `ENOTDIR` failure above for every save
  from the first launch after the directories were linked until the app
  quit five days later, and not one session file changed in that time.
  Reading the list through the link works, which is why the failure went
  unnoticed.  The code that opens the directory with `O_NOFOLLOW` is in
  the app bundle, version 2.2553.13.  The same code opens the directory
  only once per launch and then reuses the result, so a link made while
  the app is running appears to work until the app next starts.  In the
  log, the first failure came one second after that next start.
- `pgrep -f` does not reliably match the app's executable path, and
  reported the app as absent while it was plainly running.  The check in
  this script matches a captured `ps` listing instead.
- A switch where both accounts already had a history of their own, 218
  sessions and 45.  All 45 of the second account's sessions came across,
  and the app listed the second account's 36 archived sessions as
  archived, not active.
- The app decides whether a session is archived from the `isArchived`
  flag in the session's own file.  This is from reading the app's code,
  version 2.2553.13: it loads the sessions `archived-sessions.idx` lists
  after the others, and when loading is done it rewrites the index from
  the loaded sessions.
- `test-move.sh` passes.  It covers a switch where the signed-in account
  already had sessions, an archive index and agent-mode sessions of its
  own, and the switch back.  It covers three accounts, one switch with
  `move` forgotten, and the links the earlier version left, including a
  recorded directory that is itself a link and a link at another account
  while the list is already in place.  It checks that a link to an
  unrelated directory is left alone, that a link found in one tree stops
  the move before anything changes and that a rerun then completes, and
  that `move` lists only the group filings the signed-in account lacks,
  naming the account the list came from.
- Where sidebar groups are kept.  On the machine this was written for,
  the groups the app's own sidebar tools reported for each account
  matched `dframe-store` in local storage.  The filings, and not the
  empty groups, matched the record in `claude_desktop_config.json`,
  which included a group created an hour before.  The first account's
  group was still there after switching to the second and back.  That
  the page syncs `dframe-store` with claude.ai and merges the server's
  copy is from reading the page's code, not from observation.
- Claude recreated the second account's one group in the first account
  with its sidebar tools, and `groups` then showed the filing in both.
  `test-groups.py` checks `groups` on made-up records: the order within
  a group, titles taken from the session directory rather than a backup
  of it, items that are not Code sessions, empty groups left out, only
  missing filings listed after a switch, and records it cannot read or
  does not recognize.
- `move` and then `recover --apply` on the machine where the failure
  happened.  `move` removed the two links, and `recover` wrote the 81
  missing sessions and updated 23.  On relaunch the app loaded all 1163
  session files, 23 active and 1140 archived, recovered sessions resumed
  their conversations, and no save has failed since.

Not verified, and worth knowing:

- Whether the app re-reads the session directory while running, or only
  at startup.  The instructions above say to quit and reopen the app,
  which is correct either way.
- Whether future versions of the app keep this layout, or the log lines
  `recover` reads.  Nothing here is a published interface.  A version
  that changes where the session list lives will break `move`, and
  `status` will show it: the entry counts will not match what the
  sidebar shows.  A version that changes the log lines will make
  `recover --verify` report mismatches.
