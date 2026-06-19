#!/usr/bin/env python3
"""Announce new GitHub pull requests in a Discord channel (CGI version).

Apache runs this script once per webhook delivery. It reads the delivery from
the CGI environment and standard input, keeps the ones that are a pull request
opened by a watched author, and posts a one-line message to a Discord channel
through an incoming webhook.

The script starts when a webhook arrives and exits once it has handled it.

All deployment-specific values live in an INI config file. By default this is a
file named .config in this script's own directory; set the CONFIG environment
variable to point somewhere else. The only JSON involved is the GitHub payload
coming in and the Discord payload going out, because those services require it.
"""

import configparser
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Discord webhook message flag that hides the link preview, so the message
# stays as the plain "URL <- title" line.
SUPPRESS_EMBEDS = 4


class Config:
    def __init__(self, path):
        if not os.path.exists(path):
            raise SystemExit(f"config file not found: {path}")
        parser = configparser.ConfigParser()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                parser.read_string(handle.read())
        except OSError as error:
            # The config is mode 0400, so only its owner can read it. A
            # permission error here means the CGI is not running as that owner.
            raise SystemExit(f"cannot read config {path}: {error}")
        self.discord_webhook = parser.get("discord", "webhook_url")
        self.secret = parser.get("github", "secret", fallback="").encode()
        self.authors = self._lower_list(parser.get("github", "authors", fallback=""))
        self.orgs = self._lower_list(parser.get("github", "orgs", fallback=""))
        self.actions = self._lower_list(parser.get("github", "actions", fallback="opened"))
        self.template = parser.get("message", "template", fallback="{url} <- {title}")
        self.suppress_embeds = parser.getboolean("message", "suppress_embeds", fallback=True)

    @staticmethod
    def _lower_list(value):
        return [item.strip().lower() for item in value.split(",") if item.strip()]


def log(message):
    # Standard output is the HTTP response, so logs go to standard error, which
    # Apache writes to its error log.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def respond(status):
    sys.stdout.write(f"Status: {status}\r\n")
    sys.stdout.write("Content-Type: text/plain\r\n")
    sys.stdout.write("Content-Length: 0\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.flush()


def signature_ok(config, raw_body, header_value):
    """Check GitHub's HMAC-SHA256 signature over the raw request body."""
    if not config.secret:
        return True
    if not header_value or not header_value.startswith("sha256="):
        return False
    digest = hmac.new(config.secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + digest, header_value)


def post_to_discord(config, content):
    payload = {"content": content}
    if config.suppress_embeds:
        payload["flags"] = SUPPRESS_EMBEDS
    request = urllib.request.Request(
        config.discord_webhook,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "pr-discord-notifier",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        log(f"discord rejected the message: {error.code} {detail}")
    except urllib.error.URLError as error:
        log(f"could not reach discord: {error.reason}")


def handle_pull_request(config, payload):
    action = str(payload.get("action", "")).lower()
    if config.actions and action not in config.actions:
        return
    pull_request = payload.get("pull_request") or {}
    author = str((pull_request.get("user") or {}).get("login", "")).lower()
    if config.authors and author not in config.authors:
        return
    repository = payload.get("repository") or {}
    org = str((repository.get("owner") or {}).get("login", "")).lower()
    if config.orgs and org not in config.orgs:
        return
    url = pull_request.get("html_url", "")
    title = pull_request.get("title", "")
    if not url:
        return
    log(f"announcing {url}")
    post_to_discord(config, config.template.format(url=url, title=title))


def main():
    config_path = os.environ.get("CONFIG") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".config"
    )
    config = Config(config_path)

    if os.environ.get("REQUEST_METHOD") != "POST":
        respond("405 Method Not Allowed")
        return

    length = int(os.environ.get("CONTENT_LENGTH") or 0)
    raw_body = sys.stdin.buffer.read(length)

    if not signature_ok(config, raw_body, os.environ.get("HTTP_X_HUB_SIGNATURE_256")):
        log("rejected a delivery with a bad signature")
        respond("401 Unauthorized")
        return

    if os.environ.get("HTTP_X_GITHUB_EVENT") != "pull_request":
        # "ping" (sent when the webhook is created) and everything else are
        # acknowledged and ignored.
        respond("204 No Content")
        return

    try:
        payload = json.loads(raw_body)
    except ValueError:
        respond("400 Bad Request")
        return

    # Acknowledge the delivery, then post to Discord.
    respond("204 No Content")
    handle_pull_request(config, payload)


if __name__ == "__main__":
    main()
