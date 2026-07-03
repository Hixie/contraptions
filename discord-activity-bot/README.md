# discord-activity-bot

Scan a Discord server with a bot token and write an HTML activity report with
one sparkline per message-bearing channel.

The scanner counts message timestamps and authors. Generated reports do not
store message bodies. By default it includes top-level text channels,
announcement channels, voice channel text chat, stage channel text chat, forum
posts, media posts, active threads, public archived threads, and joined private
archived threads the bot can list.
Thread activity is collapsed into the parent channel row.

The default scan window is the full lifetime of the server, from the server
creation time encoded in the guild ID through the time the report starts.

## Permissions

Create a Discord application, add a bot, and invite it to the server with these
permissions:

- View Channels
- Read Message History
- Connect

Connect is included because Discord uses it for voice and stage channel text
chat history.

The invite permission integer for those permissions is `1115136`.

Use this invite URL after replacing `CLIENT_ID`:

```text
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=1115136
```

If you want the bot to post the report into Discord with `--post-channel-id`,
also grant Send Messages. The permission integer with Send Messages included is
`1117184`.

The Message Content privileged intent is not needed. Private channels and
private threads are only counted when the bot can view them. Discord can return
an empty message list when a per-channel overwrite removes Read Message History.
The scanner warns when Discord reports a last message but returns no history.

## Run

From this directory:

```sh
export DISCORD_BOT_TOKEN='your bot token'
export DISCORD_GUILD_ID='1232452732963655720'

python3 -m discord_activity_bot > report.html
```

Open `report.html` in a browser. The default report is a complete HTML page.
Click a column heading to sort the table by that column. Click the same heading
again to reverse the sort order. Numeric columns sort descending on the first
click. The table has these columns:

- Channel, without the category prefix.
- Activity, with a shared logarithmic scale.
- Created.
- First.
- Last.
- Msgs.
- Rate, measured as messages per day since the channel was created.
- People.
- Top Sender.

With the default settings, the scan window is the server's entire lifetime.

Every sparkline uses the same time window, bucket size, visual width, and
logarithmic vertical message-count scale. The default bucket size is one week,
which keeps a full-lifetime report readable. Use `--bucket day` when you want
daily detail.

Use `--json` for a structured report:

```sh
python3 -m discord_activity_bot --days 14 --bucket hour --json
```

## Raw data workflow

For expensive lifetime scans, save the raw Discord responses once:

```sh
python3 -m discord_activity_bot --save-raw raw-discord.json --scan-only
```

Then generate reports from that file without contacting Discord again:

```sh
python3 -m discord_activity_bot --from-raw raw-discord.json > report.html
python3 -m discord_activity_bot --from-raw raw-discord.json --bucket day > daily.html
python3 -m discord_activity_bot --from-raw raw-discord.json --json > report.json
```

The raw file contains the Discord API responses the bot downloaded, including
message objects available to the bot. Treat it as private server data.

Use `--text` for the compact terminal table:

```sh
python3 -m discord_activity_bot --text
```

To post the report into Discord after printing it, give the bot Send Messages in
the destination channel and pass that channel ID. Discord gets the compact text
table, while standard output still gets the selected format.

```sh
python3 -m discord_activity_bot --post-channel-id CHANNEL_ID
```

You can also set `DISCORD_REPORT_CHANNEL_ID` instead of passing
`--post-channel-id`.

## Useful options

- `--days 7` limits the scan to recent history instead of the full server lifetime.
- `--bucket hour`, `--bucket day`, or `--bucket week` changes each sparkline cell.
  The default is `week`.
- `--exclude-bots` skips messages authored by bots.
- `--text` prints the compact terminal table instead of HTML.
- `--json` prints machine-readable JSON instead of HTML.
- `--save-raw raw-discord.json` writes raw Discord responses during a scan.
- `--scan-only` writes `--save-raw` without printing a report.
- `--from-raw raw-discord.json` generates a report from a saved raw scan.
- `--no-threads` counts only top-level channel messages.
- `--no-archived-threads` counts top-level messages and active threads only.
- `--no-private-archived-threads` skips joined private archived threads.
- `--include-all-private-archived-threads` asks Discord for all private archived
  threads in visible parent channels. This requires Manage Threads. The
  permission integer with the default read permissions is `17180984320`.
- `--ascii` uses plain characters in the sparkline.

## Test

```sh
python3 -m unittest discover
```

## Notes

Discord returns at most 100 messages per request. Large servers and lifetime
scan windows can take a while. The scanner waits when Discord returns a
rate-limit response, then retries the request. Daily or hourly lifetime reports
can create very wide output on older servers.

Voice and stage attendance history is not available through the message history
API. Voice channels are included for their text chat only.
