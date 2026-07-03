from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone
from typing import Any

from .scanner import ChannelReport, ScanResult


SPARK_LEVELS = "▁▂▃▄▅▆▇█"
ASCII_LEVELS = "12345678"


def make_sparkline(
    counts: list[int],
    *,
    ascii_only: bool = False,
    peak: int | None = None,
) -> str:
    zero = "." if ascii_only else "·"
    levels = ASCII_LEVELS if ascii_only else SPARK_LEVELS
    if not counts:
        return ""
    scale_peak = max(counts) if peak is None else peak
    if scale_peak <= 0:
        return zero * len(counts)
    sparkline = []
    for count in counts:
        if count <= 0:
            sparkline.append(zero)
            continue
        level_index = max(
            math.ceil(log_scaled_fraction(count, scale_peak) * len(levels)) - 1,
            0,
        )
        level_index = min(level_index, len(levels) - 1)
        sparkline.append(levels[level_index])
    return "".join(sparkline)


def format_text_report(result: ScanResult, *, ascii_only: bool = False) -> str:
    rows = [channel for channel in result.channels]
    name_width = min(max([len(channel.path) for channel in rows] + [7]), 46)
    kind_width = min(max([len(channel.kind) for channel in rows] + [4]), 14)
    message_width = max(len(str(max([channel.message_count for channel in rows] + [0]))), 8)
    activity_width = len(rows[0].counts) if rows else len("Activity")
    peak = global_peak(rows)

    lines = [
        f"Discord activity for guild {result.guild_id}",
        f"Window: {display_time(result.start)} to {display_time(result.end)}",
        f"Bucket: {display_bucket(result.bucket_seconds)}",
        f"Threads: {thread_summary(result)}",
        "",
        f"{'Channel'.ljust(name_width)}  {'Type'.ljust(kind_width)}  "
        f"{'Messages'.rjust(message_width)}  Activity",
        f"{'-' * name_width}  {'-' * kind_width}  {'-' * message_width}  "
        f"{'-' * activity_width}",
    ]
    for channel in rows:
        lines.append(
            f"{shorten(channel.path, name_width).ljust(name_width)}  "
            f"{shorten(channel.kind, kind_width).ljust(kind_width)}  "
            f"{str(channel.message_count).rjust(message_width)}  "
            f"{make_sparkline(channel.counts, ascii_only=ascii_only, peak=peak)}"
        )

    if result.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in result.warnings)

    return "\n".join(lines)


def format_html_report(result: ScanResult) -> str:
    rows = [channel for channel in result.channels]
    peak = global_peak(rows)
    bucket_count = len(rows[0].counts) if rows else result_bucket_count(result)
    total_messages = sum(channel.message_count for channel in rows)
    total_participants = len(
        {
            participant_id
            for channel in rows
            for participant_id in channel.participant_counts
        }
    )
    generated = display_time(result.end)
    title = f"Discord activity for guild {result.guild_id}"
    body_rows = "\n".join(html_channel_row(channel, result, peak) for channel in rows)
    warnings = html_warnings(result.warnings)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --page: #f6f8f7;
      --ink: #15201d;
      --muted: #5e6b66;
      --line: #d9e0dc;
      --panel: #ffffff;
      --accent: #0f766e;
      --accent-strong: #0b4f49;
      --warm: #b45309;
      --bar-low: #a7d7d1;
      --bar-high: #0f766e;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      min-width: 980px;
      background:
        linear-gradient(180deg, #eef7f3 0, var(--page) 360px),
        var(--page);
      color: var(--ink);
      font-family:
        Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }}

    main {{
      width: 100%;
      margin: 0;
      padding: 28px 0 44px;
    }}

    header {{
      width: min(1480px, calc(100vw - 48px));
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 24px;
      align-items: end;
      margin: 0 auto 20px;
    }}

    h1 {{
      margin: 0 0 10px;
      font-size: 32px;
      line-height: 1.12;
      letter-spacing: 0;
    }}

    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.5;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(112px, 1fr));
      gap: 1px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--line);
      box-shadow: 0 14px 40px rgba(21, 32, 29, 0.08);
    }}

    .metric {{
      min-width: 112px;
      padding: 14px 16px;
      background: rgba(255, 255, 255, 0.86);
    }}

    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .metric strong {{
      display: block;
      margin-top: 5px;
      font-size: 18px;
      line-height: 1.2;
    }}

    .table-shell {{
      overflow: auto;
      border: 1px solid var(--line);
      border-right: 0;
      border-left: 0;
      border-radius: 0;
      background: var(--panel);
      box-shadow: 0 18px 60px rgba(21, 32, 29, 0.08);
    }}

    table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      font-size: 14px;
    }}

    th, td {{
      border-bottom: 1px solid var(--line);
      vertical-align: middle;
      text-align: left;
      white-space: nowrap;
    }}

    th {{
      padding: 8px 12px;
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f9fbfa;
      color: #33423d;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}

    td {{
      padding: 0 12px;
    }}

    th button {{
      all: unset;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 4px;
      padding: 2px 0;
    }}

    th button:focus-visible {{
      outline: 2px solid var(--accent-strong);
      outline-offset: 3px;
    }}

    th button::after {{
      content: "";
      width: 0.9em;
      color: var(--accent-strong);
      font-size: 13px;
      font-weight: 600;
      line-height: 1;
    }}

    th button[data-direction="asc"]::after {{
      content: "↑";
    }}

    th button[data-direction="desc"]::after {{
      content: "↓";
    }}

    tr:last-child td {{ border-bottom: 0; }}
    tbody tr:nth-child(even) td {{ background: #fbfcfb; }}
    tbody tr:hover td {{ background: #f0faf7; }}

    .channel {{
      min-width: 260px;
      max-width: 420px;
      white-space: normal;
    }}

    .channel-name {{
      display: block;
      font-weight: 700;
      line-height: 1.3;
    }}

    .spark-cell {{
      min-width: 320px;
      width: 34vw;
    }}

    .sparkline {{
      display: grid;
      grid-template-columns: repeat({bucket_count}, minmax(2px, 1fr));
      align-items: end;
      gap: 2px;
      width: 100%;
      height: 40px;
      padding: 4px 0 2px;
    }}

    .spark-bar {{
      min-width: 2px;
      height: var(--bar-height);
      border-radius: 3px 3px 1px 1px;
      background: transparent;
      opacity: var(--bar-opacity);
    }}

    .spark-bar.is-active {{
      min-height: 2px;
      background:
        linear-gradient(180deg, var(--bar-high), var(--bar-low));
    }}

    .sr-only {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}

    .age, .number {{
      font-variant-numeric: tabular-nums;
    }}

    .participant {{
      max-width: 240px;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .participant-count {{
      color: var(--muted);
    }}

    .warnings {{
      width: min(1480px, calc(100vw - 48px));
      margin: 20px auto 0;
      padding: 16px 18px;
      border: 1px solid #f1c78a;
      border-radius: 8px;
      background: #fff8ed;
      color: #653a07;
    }}

    .warnings h2 {{
      margin: 0 0 8px;
      font-size: 15px;
    }}

    .warnings ul {{
      margin: 0;
      padding-left: 20px;
    }}

    @media (max-width: 900px) {{
      body {{ min-width: 0; }}
      main {{
        width: 100%;
        padding-top: 18px;
      }}
      header {{
        grid-template-columns: 1fr;
        width: calc(100vw - 24px);
      }}
      .summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .spark-cell {{
        min-width: 260px;
        width: 260px;
      }}
      .warnings {{
        width: calc(100vw - 24px);
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{escape(title)}</h1>
        <p class="subtitle">
          Window: {escape(display_time(result.start))} to {escape(generated)}.
          Bucket: {escape(display_bucket(result.bucket_seconds))}.
          Threads: {escape(thread_summary(result))}.
          Message totals and participants use this same window.
        </p>
      </div>
      <div class="summary" aria-label="Report summary">
        <div class="metric"><span>Channels</span><strong>{len(rows)}</strong></div>
        <div class="metric"><span>Messages</span><strong>{format_number(total_messages)}</strong></div>
        <div class="metric"><span>Participants</span><strong>{format_number(total_participants)}</strong></div>
        <div class="metric"><span>Log Scale Peak</span><strong>{format_number(peak)}</strong></div>
      </div>
    </header>

    <div class="table-shell">
      <table data-sortable-table>
        <thead>
          <tr>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="channel" data-sort-type="text">Channel</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="sparkline" data-sort-type="number">Activity (log)</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="created" data-sort-type="number">Created</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="first" data-sort-type="number">First</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="last" data-sort-type="number">Last</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="messages" data-sort-type="number">Msgs</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="message_rate" data-sort-type="number">Rate</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="participants" data-sort-type="number">People</button></th>
            <th scope="col" aria-sort="none"><button type="button" data-sort-key="prolific" data-sort-type="text">Top Sender</button></th>
          </tr>
        </thead>
        <tbody>
{body_rows}
        </tbody>
      </table>
    </div>
{warnings}
  </main>
  <script>
    (() => {{
      const table = document.querySelector("[data-sortable-table]");
      if (!table) return;
      const tbody = table.querySelector("tbody");
      const buttons = Array.from(table.querySelectorAll("th button"));
      let activeKey = "";
      let activeDirection = 1;
      const collator = new Intl.Collator(undefined, {{
        numeric: true,
        sensitivity: "base",
      }});

      buttons.forEach((button) => {{
        button.addEventListener("click", () => {{
          const key = button.dataset.sortKey || "";
          const type = button.dataset.sortType || "text";
          activeDirection = activeKey === key ? -activeDirection : initialDirection(type);
          activeKey = key;
          buttons.forEach((item) => {{
            item.removeAttribute("data-direction");
            item.closest("th")?.setAttribute("aria-sort", "none");
          }});
          button.setAttribute("data-direction", activeDirection === 1 ? "asc" : "desc");
          button.closest("th")?.setAttribute(
            "aria-sort",
            activeDirection === 1 ? "ascending" : "descending",
          );
          const rows = Array.from(tbody.querySelectorAll("tr"));
          rows.sort((left, right) => compareRows(left, right, key, type) * activeDirection);
          rows.forEach((row) => tbody.appendChild(row));
        }});
      }});

      function compareRows(left, right, key, type) {{
        const leftValue = left.getAttribute(`data-sort-${{key}}`) || "";
        const rightValue = right.getAttribute(`data-sort-${{key}}`) || "";
        if (type === "number") {{
          return Number(leftValue) - Number(rightValue);
        }}
        return collator.compare(leftValue, rightValue);
      }}

      function initialDirection(type) {{
        return type === "number" ? -1 : 1;
      }}
    }})();
  </script>
</body>
</html>
"""


def result_to_json(result: ScanResult, *, ascii_only: bool = False) -> str:
    peak = global_peak(result.channels)
    payload: dict[str, Any] = {
        "guild_id": result.guild_id,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "bucket_seconds": result.bucket_seconds,
        "sparkline_scale": "logarithmic",
        "sparkline_scale_peak": peak,
        "include_threads": result.include_threads,
        "include_archived_threads": result.include_archived_threads,
        "include_private_archived_threads": result.include_private_archived_threads,
        "include_all_private_archived_threads": result.include_all_private_archived_threads,
        "exclude_bots": result.exclude_bots,
        "channels": [
            channel_to_json(channel, ascii_only=ascii_only, peak=peak, end=result.end)
            for channel in result.channels
        ],
        "warnings": result.warnings,
    }
    return json.dumps(payload, indent=2)


def channel_to_json(
    channel: ChannelReport,
    *,
    end: datetime,
    ascii_only: bool = False,
    peak: int | None = None,
) -> dict[str, Any]:
    participant = most_prolific_participant(channel)
    return {
        "channel_id": channel.channel_id,
        "path": channel.path,
        "type": channel.kind,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "first_message_at": (
            channel.first_message_at.isoformat() if channel.first_message_at else None
        ),
        "last_message_at": (
            channel.last_message_at.isoformat() if channel.last_message_at else None
        ),
        "message_count": channel.message_count,
        "message_rate_per_day": message_rate_per_day(channel, end),
        "participant_count": len(channel.participant_counts),
        "most_prolific_participant": {
            "id": participant[0],
            "label": participant[1],
            "message_count": participant[2],
        }
        if participant
        else None,
        "thread_count": channel.thread_count,
        "scanned_sources": channel.scanned_sources,
        "counts": channel.counts,
        "sparkline": make_sparkline(channel.counts, ascii_only=ascii_only, peak=peak),
        "notes": channel.notes,
    }


def split_discord_messages(text: str, *, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in text.splitlines():
        line_size = len(line) + 1
        if current and current_size + line_size > limit:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        if line_size > limit:
            chunks.extend(split_long_line(line, limit=limit))
            continue
        current.append(line)
        current_size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks


def split_long_line(line: str, *, limit: int) -> list[str]:
    return [line[start : start + limit] for start in range(0, len(line), limit)]


def html_channel_row(channel: ChannelReport, result: ScanResult, peak: int) -> str:
    participant = most_prolific_participant(channel)
    participant_html = "-"
    participant_label = ""
    if participant is not None:
        _, label, count = participant
        participant_label = label
        participant_html = (
            f"{escape(label)} <span class=\"participant-count\">"
            f"({format_number(count)})</span>"
        )
    empty_class = " spark-empty" if max(channel.counts or [0]) == 0 else ""
    rate = message_rate_per_day(channel, result.end)
    display_name = display_channel_name(channel.path)
    return f"""          <tr
            data-sort-channel="{escape(display_name.casefold())}"
            data-sort-sparkline="{channel.message_count}"
            data-sort-created="{timestamp_sort_value(channel.created_at)}"
            data-sort-first="{timestamp_sort_value(channel.first_message_at)}"
            data-sort-last="{timestamp_sort_value(channel.last_message_at)}"
            data-sort-messages="{channel.message_count}"
            data-sort-message_rate="{rate}"
            data-sort-participants="{len(channel.participant_counts)}"
            data-sort-prolific="{escape(participant_label.casefold())}">
            <td class="channel">
              <span class="channel-name" title="{escape(channel.path)}">{escape(display_name)}</span>
            </td>
            <td class="spark-cell">{html_sparkline(channel.counts, result, peak, empty_class)}</td>
            <td class="age">{html_relative_time(channel.created_at, result.end)}</td>
            <td class="age">{html_relative_time(channel.first_message_at, result.end)}</td>
            <td class="age">{html_relative_time(channel.last_message_at, result.end)}</td>
            <td class="number">{format_number(channel.message_count)}</td>
            <td class="number">{format_rate(rate)}</td>
            <td class="number">{format_number(len(channel.participant_counts))}</td>
            <td class="participant">{participant_html}</td>
          </tr>"""


def html_sparkline(
    counts: list[int],
    result: ScanResult,
    peak: int,
    empty_class: str,
) -> str:
    bars = []
    bucket_labels = []
    for index, count in enumerate(counts):
        height = bar_height(count, peak)
        opacity = "0.9" if count else "0.45"
        class_name = "spark-bar is-active" if count else "spark-bar"
        bucket_start = result.start.timestamp() + index * result.bucket_seconds
        bucket_end = min(bucket_start + result.bucket_seconds, result.end.timestamp())
        start_label = display_time(datetime.fromtimestamp(bucket_start, timezone.utc))
        end_label = display_time(datetime.fromtimestamp(bucket_end, timezone.utc))
        title = (
            f"{start_label} to {end_label}: {format_number(count)} messages"
        )
        bucket_labels.append(f"{start_label}: {format_number(count)} messages")
        bars.append(
            f"<span class=\"{class_name}\" style=\"--bar-height:{height}%;"
            f"--bar-opacity:{opacity}\" title=\"{escape(title)}\" "
            f"aria-hidden=\"true\"></span>"
        )
    label = (
        "Activity sparkline. Shared logarithmic vertical scale peak is "
        f"{format_number(peak)} messages per bucket. Counts by bucket: "
        + ", ".join(format_number(count) for count in counts)
        + "."
    )
    return (
        f"<div class=\"sparkline{empty_class}\" "
        f"role=\"img\" tabindex=\"0\" aria-label=\"{escape(label)}\">"
        + "".join(bars)
        + f"<span class=\"sr-only\">{escape('; '.join(bucket_labels))}</span>"
        + "</div>"
    )


def html_relative_time(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "-"
    exact = display_time(value)
    return (
        f"<time datetime=\"{escape(value.isoformat())}\" title=\"{escape(exact)}\">"
        f"{escape(relative_age(value, now))}</time>"
    )


def html_warnings(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "\n".join(f"        <li>{escape(warning)}</li>" for warning in warnings)
    return f"""
    <section class="warnings">
      <h2>Warnings</h2>
      <ul>
{items}
      </ul>
    </section>"""


def global_peak(channels: list[ChannelReport]) -> int:
    return max([max(channel.counts or [0]) for channel in channels] + [0])


def result_bucket_count(result: ScanResult) -> int:
    seconds = max((result.end - result.start).total_seconds(), 1)
    return max(math.ceil(seconds / result.bucket_seconds), 1)


def bar_height(count: int, peak: int) -> float:
    if count <= 0 or peak <= 0:
        return 0.0
    return round(log_scaled_fraction(count, peak) * 100, 2)


def log_scaled_fraction(count: int, peak: int) -> float:
    if count <= 0 or peak <= 0:
        return 0.0
    return min(math.log1p(count) / math.log1p(peak), 1.0)


def most_prolific_participant(channel: ChannelReport) -> tuple[str, str, int] | None:
    if not channel.participant_counts:
        return None
    participant_id, count = sorted(
        channel.participant_counts.items(),
        key=lambda item: (
            -item[1],
            channel.participant_labels.get(item[0], item[0]).casefold(),
            item[0],
        ),
    )[0]
    return participant_id, channel.participant_labels.get(participant_id, participant_id), count


def message_rate_per_day(channel: ChannelReport, end: datetime) -> float:
    if channel.created_at is None:
        return 0.0
    duration_days = max((end - channel.created_at).total_seconds() / 86400, 1 / 86400)
    return channel.message_count / duration_days


def format_rate(value: float) -> str:
    if value >= 100:
        return f"{value:,.0f}/day"
    if value >= 10:
        return f"{value:,.1f}/day"
    return f"{value:,.2f}/day"


def timestamp_sort_value(value: datetime | None) -> float:
    if value is None:
        return -1
    return value.timestamp()


def display_channel_name(path: str) -> str:
    marker_positions = [
        path.rfind(marker)
        for marker in ("/#", "/voice:", "/stage:")
    ]
    marker_start = max(marker_positions)
    if marker_start == -1:
        return path
    return path[marker_start + 1 :]


def relative_age(value: datetime, now: datetime) -> str:
    seconds = max(int((now - value).total_seconds()), 0)
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 45:
        return f"{days}d"
    months = days // 30
    if months < 18:
        return f"{max(months, 1)}mo"
    years = days // 365
    return f"{max(years, 1)}y"


def format_number(value: int) -> str:
    return f"{value:,}"


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def display_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def display_bucket(seconds: int) -> str:
    if seconds % 604800 == 0:
        amount = seconds // 604800
        unit = "week" if amount == 1 else "weeks"
        return f"{amount} {unit}"
    if seconds % 86400 == 0:
        amount = seconds // 86400
        unit = "day" if amount == 1 else "days"
        return f"{amount} {unit}"
    if seconds % 3600 == 0:
        amount = seconds // 3600
        unit = "hour" if amount == 1 else "hours"
        return f"{amount} {unit}"
    return f"{seconds} seconds"


def thread_summary(result: ScanResult) -> str:
    if not result.include_threads:
        return "not included"
    archived = "public archived included" if result.include_archived_threads else "active only"
    if result.include_private_archived_threads:
        archived += ", joined private archived included"
    if result.include_all_private_archived_threads:
        archived += ", all private archived included"
    return archived


def shorten(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."
