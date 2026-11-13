# quotabar

A macOS menu bar indicator that shows, for each of several Claude accounts,
how much of the account's weekly quota has been used and how long remains
until the quota resets.

## What it shows

Each account has a small circle in the menu bar:

- The **outer ring** fills clockwise from the top as the week passes. It is
  empty just after the weekly quota resets, and full just before the next
  reset.
- The **inner disc** fills clockwise from the top as the quota is used. It is
  empty when none of the quota is used, and full when all of it is.

When the disc is further round than the ring, the account is using its quota
faster than the week is passing, and will run out before the reset if it
carries on at that rate.

A digit in the middle of a circle, drawn in reverse video against the disc,
gives how many banked resets the account has that clear its weekly limit, or
"+" for more than nine; there is no digit when it has none. A banked reset is
one the account can use to clear a limit early, which Anthropic grants to some
accounts. quotabar counts weekly resets and full resets, which clear every
limit, and leaves out five-hour resets and Fable resets, which give back only
what Fable used. It sorts resets into these kinds the way Claude Code does.

An account whose quota nobody has used yet this week may have no reset time,
and then its ring stays empty. A circle drawn faintly means the last check of
that account failed, or that its reset has passed since the last successful
check, and shows what that check found; with no successful check yet, it is a
faint disc. A circle with an exclamation mark means the account needs signing
in again.

The circles are in order of when, in the week, each account's quota resets,
counting from Monday midnight. quotabar keeps each account's last known reset
time with the account, so the order stays the same across restarts and through
weeks the server reports no reset time for. While an account's quota keeps
resetting at the same time each week, its place does not change. Monday
midnight is taken in the local time zone's standard time, so a change to or
from daylight saving time does not change the order either. Accounts whose
reset times are not known yet come last, in the order they were added.

Click the circles for a menu with a line for each account, giving its email
address, the percentage of the quota used, the time until the reset, and how
many banked resets it has. Each line has a submenu with the account's
organization, the day and time of the reset, each banked reset it counts with
how many are left and when they expire, when the last successful check was,
any problem with the last check, and items to sign in to the account again or
remove it. quotabar only shows banked resets; it never uses them. When an
account has no banked resets to show, its submenu says why: the answer says
nothing about them, the account is not eligible for them (with the reason the
answer gives, if any), it has none that clear the weekly limit, or quotabar
could not read them, in which case the rest still shows. It also names any
kind of banked reset quotabar does not know.

## Adding accounts

Choose **Add Account…**. quotabar opens Claude's sign-in page in the default
browser. Sign in there as the account to add, and the browser comes back to
quotabar, which adds the account under its email address. The sign-in page
uses the account the browser is already signed in to, if any, so to add
another account, first sign the browser in to that account on claude.ai.

Signing in to an account that quotabar already has, for example after it shows
an exclamation mark, replaces the account's tokens and keeps its place. **Sign
In Again…** in an account's submenu asks the sign-in page for that account by
its email address. If the browser signs in to a different account anyway,
quotabar adds or updates that account and says so in the menu, as it does when
a sign-in fails. The same email address in two organizations counts as two
accounts.

The sign-in page asks whether "Claude Code" may connect to the account. That
is because quotabar signs in as Claude Code's own client, described below: the
page names the client by its registration with Anthropic, and only Anthropic
can register a client under another name.

## How it works

quotabar signs in the way Claude Code does: through the browser, with OAuth
and PKCE, as Claude Code's own client, with the browser returning to a
listener that quotabar opens on the loopback interface for the length of the
sign-in. It asks only for the `user:profile` scope, which is enough to read
usage. It does not ask for the scope for running models.

quotabar keeps its own tokens, separate from Claude Code's. Each use of a
refresh token replaces it with a new one and stops the old one working, so
sharing Claude Code's tokens would make the two programs sign each other out.

Every five minutes, and whenever the Mac wakes, quotabar asks
`https://api.anthropic.com/api/oauth/usage` about each account, the endpoint
behind Claude Code's `/usage` command, and reads the answer's weekly quota. It
adds `?cedar_ember=1&skip_spend=1`, as Claude Code does to ask for banked
resets. Claude Code treats banked resets as an experiment that is still
rolling out, so accounts outside it report none.

The server reports banked resets only to the products it offers them in, which
it tells apart by the headers a request carries; to anything else it says the
account is not eligible, giving "surface" as the reason. So every request
quotabar sends carries Claude Code's headers, `User-Agent: claude-cli/2.1.311
(external, cli) quotabar/1` and `x-app: cli`, with quotabar's own name added
to the first. This presents quotabar to Anthropic as Claude Code, which
Anthropic's terms may not allow.

The endpoint has no way to tell quotabar when usage changes, so quotabar has
to ask. **Check Now** asks straight away. macOS tells quotabar when the Mac
goes offline and comes back, and checks asked for while it is offline, as it
often is just after waking, wait until it is online. A check that fails for
want of a network (no connection, a lost connection, a timeout, or a host that
cannot be found or reached), as it can before macOS has noticed the network
go, runs again once macOS says the network is there: at once, if macOS said so
while the check ran. When an access token has expired, or the endpoint refuses
it, quotabar refreshes it first. Only a refusal of the refresh token itself,
which the token endpoint reports as `invalid_grant`, means the account needs
signing in again, and quotabar stops checking such an account until it is
signed in to again, or until it restarts. An account is never checked twice at
once with the same tokens, since both checks could spend the same refresh
token. A refresh answer whose other parts cannot be read still has its new
refresh token saved, since the server has already spent the old one. Quitting,
from the menu or by the SIGTERM signal launchd sends, starts no more checks
and waits for any refresh under way to finish and an attempt to save its
tokens. A request fails once the server has been silent for a minute, though
one that keeps sending slowly can take longer; the launchd agent gives
quotabar 90 seconds to quit before stopping it.

Only one quotabar runs at a time: each holds a lock on `~/Library/Application
Support/quotabar/lock`, and one that finds the lock taken exits straight away.
Two would spend each other's refresh tokens.

The endpoint is not documented, so it could change without notice.

## Where the accounts are kept

All the accounts and their tokens are kept together in one item in the login
keychain, under the name `quotabar`. macOS lets the program that created a
keychain item read it without asking. A rebuilt quotabar counts as a new
program, so after a rebuild macOS asks once whether quotabar may read the
item. Choose **Always Allow** so that it does not ask again until the next
rebuild.

If quotabar cannot save the item, the menu says why and the menu bar adds a
bare exclamation mark, with no ring, after any circles, since a refresh token
that was not saved is lost when quotabar next starts. quotabar tries again
whenever it would check, even while offline, and when quitting.

If quotabar cannot read the item, for example because access was denied, the
menu bar shows only a bare exclamation mark, the menu says why, and offers
**Read the Keychain Again** in place of **Add Account…**, so that quotabar
cannot replace accounts it never read.

If the item holds anything quotabar does not know, such as a field a newer
quotabar added, quotabar refuses to read it rather than drop that field the
next time it saves, and says where the field is. It also refuses an item that
lists the same account twice.

Removing an account only forgets its tokens. It does not end the sign-in on
Anthropic's side.

## Building and running

Requires the Xcode command line tools (for `swiftc`).

```sh
make            # builds ./quotabar
./quotabar      # runs the menu bar indicator in the foreground, unless one is running
make test       # builds and runs the tests
```

The tests cover everything except [main.swift](main.swift), which starts the
program and connects the menu bar item, the timer, and quitting to the rest.
They draw the circles into bitmaps and check their pixels, and choose items
from the menu. They talk to fake servers, except for listeners they open on
the loopback interface, and they add and then delete items in the login
keychain. Each run's items name its process, and a run deletes any items left
by runs whose processes have gone.

## Installing as a login item

```sh
make install
```

This copies the binary to `~/.local/bin/quotabar`, generates a launchd user
agent at `~/Library/LaunchAgents/local.quotabar.plist` from the template in
this directory, and starts it. The agent starts the indicator at login and
restarts it if it crashes; quitting from the menu (a clean exit) does not
trigger a restart. Running `make install` again rebuilds, reinstalls, and
restarts the running indicator.

```sh
make uninstall
```

This stops the indicator and removes both the agent plist and the installed
binary. The keychain item stays; delete it with Keychain Access, or with
`security delete-generic-password -s quotabar`.
