# claude-account-switch

Carries the Claude desktop app's session list from one Claude account to
another, so that signing out of one account and into a second one does not
leave you looking at an empty sidebar.

Everything this script touches is a local file under your home directory.
It shares one on-disk index between two accounts you are already entitled
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
making the second account's directory a symbolic link to the first
account's directory.

A symbolic link rather than a copy has two consequences worth having.
Switching back and forth later needs no further work, because both
accounts read and write one shared list.  And the list is not duplicated,
which on the machine this was written for meant not making a second copy
of 85 MB.

## Running it

The three subcommands are meant to be run in this order, around the point
where you sign out of one account and into the other.

```bash
./account-switch.sh status   # who is recorded, who is signed in
./account-switch.sh record   # while still signed in as the first account
./account-switch.sh link     # after signing in as the second account
```

The whole procedure:

1. Run `record` while still signed in as the first account.  It writes
   the account and organization identifiers to
   `~/.claude-account-switch-state`, which is the only state the script
   keeps.  That file stays in your home directory and is not part of this
   repository, because it names your accounts.
2. Sign out in the app, sign in as the second account, and let the app
   finish opening.  It needs to have opened at least once as the second
   account, because `link` finds the organization identifier by looking
   at the directory the app creates when it signs in.
3. Quit the app completely.  `link` refuses to run while the app is
   running, because it moves files the app writes to as it goes.
4. Run `link`.
5. Open the app again.  The full session list is there.

`status` is safe to run at any time and changes nothing.  It prints the
recorded account and the signed-in account side by side, with the entry
count for each directory, or the symbolic link target where a link is
already in place.  Running it is the quickest way to see whether a switch
has actually taken effect.

`link` is idempotent.  Run a second time it reports the links it finds and
does nothing else.

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

## What `link` does with files the second account already wrote

By the time `link` runs, the second account has normally written a session
file or two of its own, plus whatever small files the app creates at
startup.  Each entry is moved into the shared directory.  An entry whose
name is already taken in the shared directory is set aside instead of
being written over, so the copy that has your history in it always wins.

The directory it is set aside in is made fresh for each run, named after
the original with `.superseded.` and six random characters on the end:

```
.../claude-code-sessions/<account>/<org>.superseded.a4Kf2p/
```

A fresh directory per run is what makes switching between the same two
accounts more than once safe.  Under a single fixed name, the second
switch would write over what the first one set aside, which is the only
way this script could destroy anything.

Nothing is deleted.  Once the app has come back up and the session list
looks right, those directories can be removed by hand.

If the directory turns out not to be empty after the moves, the script
says so and leaves it alone rather than replacing it.

`link` refuses outright when the recorded directory and the directory the
app is using turn out to be one and the same directory.  That is the
state you reach by running `record` while the two accounts are already
sharing a list and then switching back: every entry would compare as
already present in itself, and the entire session list would be set
aside.  When nothing at all could be linked, `link` says so and exits
with a failure status rather than telling you to reopen the app.

## What it deliberately does not touch

The transcripts, because they are not account-scoped and were never at
risk.

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
- The app reads and writes through the symbolic link without complaint.
  The session that produced this documentation was itself running under
  the second account, listing the first account's sessions, and its own
  session file was written into the shared directory through the link.
- `pgrep -f` does not reliably match the app's executable path, and
  reported the app as absent while it was plainly running.  The check in
  this script matches a captured `ps` listing instead.

Not verified, and worth knowing:

- Whether the app re-reads the session directory while running, or only
  at startup.  The instructions above say to quit and reopen the app,
  which is correct either way.
- Whether future versions of the app keep this layout.  Nothing here is a
  published interface, and a version that changes where the session list
  lives will break this script.  `status` will show it: the entry counts
  will not match what the sidebar shows.
