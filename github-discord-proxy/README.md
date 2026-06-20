# pr-discord-notifier

Post a line to a Discord channel whenever a pull request is opened in the GitHub
repositories you care about.

When a pull request is opened in a repository you watch, the bot posts a single
line to a Discord channel:

```
https://github.com/octo-org/octo-repo/pull/42 <- Add retry to the uploader
```

That line is just the pull request URL, an arrow, and the pull request title. A
config file decides which repository owners and which authors to include — one,
several, or everyone — and sets the message format.

It runs as a CGI script under Apache. Apache starts the script when GitHub
delivers a webhook, and the script exits once it has handled that delivery.

The only dependency is Python 3; everything the script uses is in the standard
library.

## How it works

```
GitHub  --webhook-->  Apache + index.cgi  --webhook-->  Discord channel
```

1. GitHub sends a signed HTTP POST to the script whenever a pull request changes
   in a repository the webhook is attached to.
2. Apache runs `index.cgi`. The script checks the signature, keeps the matching
   "pull request opened" events, and builds one line of text.
3. The script posts that line to a Discord incoming webhook, which makes it
   appear in the channel.

## Requirements

- A host reachable from the public internet, because GitHub connects in to
  deliver each webhook.
- Apache with CGI enabled (`mod_cgi` or `mod_cgid`) and permission to run a CGI
  script from a web directory.
- Python 3.
- A Discord channel where you can create an incoming webhook.
- Permission to add a webhook to the GitHub repositories or organization you want
  to watch.

These are all available on typical shared hosting.

## Layout

```
github-webhook/
  index.cgi    the script
  .htaccess    turns on CGI here and denies web access to .config
  .config      your secrets (you create this; gitignored)
config.example.ini
```

## Setup

### 1. Create a Discord webhook

In Discord, open **Server Settings -> Integrations -> Webhooks** and create a
new webhook. Point it at the channel where the messages should appear and copy
its URL. Treat this URL as a secret: anyone who has it can post to that channel.

### 2. Choose a GitHub webhook secret

This is any random string. It lets the script confirm that each delivery really
came from GitHub. One way to generate one:

```
openssl rand -hex 32
```

### 3. Install the CGI endpoint

Copy the `github-webhook` directory into your web document root. On many shared
hosts this is a directory such as `~/public_html`:

```
cp -r github-webhook ~/public_html/
chmod +x ~/public_html/github-webhook/index.cgi
```

The endpoint is then `https://your-host/github-webhook/index.cgi`.

### 4. Create the config file

Copy the example to `.config` next to the script, fill in your values, and make
it readable only by its owner:

```
cp config.example.ini ~/public_html/github-webhook/.config
"$EDITOR" ~/public_html/github-webhook/.config
chmod 400 ~/public_html/github-webhook/.config    # -r--------
```

At a minimum set `webhook_url` (from step 1) and `secret` (from step 2).

Two layers keep the config private. The included `.htaccess` denies web requests
for `.config`. The `0400` permission means only the file's owner can read it, so
the user Apache uses to serve static files cannot read it. The CGI script reads
it because, with per-user CGI (suEXEC), the script runs as the owner.

If a delivery later fails with "cannot read config" in the Apache error log, the
CGI is running as a different user than the one that owns the file. Either relax
the permission (for example `chmod 440` with the file in a group the CGI user
belongs to) or ask the host which user CGI runs as.

To edit the config later: `chmod 600` it, edit, then `chmod 400` again.

### 5. Confirm CGI runs

Apache must run `index.cgi` as a program. If a request to the endpoint returns
the script's source as text, or a 403 or 500 mentioning `Options` or handlers,
the host restricts the directives in the included `.htaccess`. Ask the host to
allow `Options=ExecCGI` and `FileInfo` for the directory; that is a server
setting they control.

### 6. Add the GitHub webhook

Add the webhook to each repository you want to watch, under that repository's
**Settings -> Webhooks**; any repository admin can do this. To cover an entire
organization at once, including repositories created later, add a single
organization webhook at
`https://github.com/organizations/YOUR-ORG/settings/hooks`; this needs
organization owner access.

Set the webhook fields to:

- **Payload URL:** `https://your-host/github-webhook/index.cgi`
- **Content type:** `application/json`
- **Secret:** the string from step 2
- **Events:** choose "Let me select individual events" and tick only
  **Pull requests**.

## Testing

When you save the webhook, GitHub sends a test "ping". Open the webhook's
**Recent Deliveries** tab; a green check means the script accepted it. To
exercise the full path, open a test pull request as a watched author, or use
**Redeliver** on an earlier pull request delivery. Messages and errors from the
script appear in the Apache error log.

## Configuration

All settings live in the `.config` file, in INI format. See `config.example.ini`
for a starting point.

`[discord]`

- `webhook_url` — the Discord incoming webhook to post to.
- `username` — the name to show on each message. Empty uses the name set on the
  webhook in Discord.
- `avatar_url` — the URL of an image to show as the avatar on each message. Empty
  uses the picture set on the webhook in Discord.

`[github]`

- `secret` — the shared secret, matching the GitHub webhook's Secret field.
- `authors` — comma-separated GitHub logins to announce. Empty means every
  author.
- `orgs` — comma-separated repository owners to accept. Empty means any owner.
- `actions` — which pull request actions to announce. The default, `opened`,
  covers new pull requests, including drafts. You can add others such as
  `reopened` or `ready_for_review`.

`[message]`

- `template` — the message text. `{url}` and `{title}` are replaced with the
  pull request's URL and title.
- `suppress_embeds` — when true, Discord hides the link preview card, so the
  message is just the templated line.

## Security notes

- The script verifies GitHub's HMAC-SHA256 signature on every delivery and acts
  only on deliveries whose signature matches the secret.
- Keep `.config` out of version control. The included `.gitignore` already
  ignores it.
- The only data sent anywhere is one line per matching pull request, posted to
  the Discord webhook you configured.
