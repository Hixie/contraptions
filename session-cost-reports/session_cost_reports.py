#!/usr/bin/env python3
"""Generate local Claude and Codex reports for current or completed quota windows."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import sqlite3
from zoneinfo import ZoneInfo

import claude_context
import claude_reader
import codex_reader
from reporting import (aggregate, claude_logs, compact_quotas, iso,
                       select_window, save_report, scan_codex)


def local_zone():
    name = os.environ.get('TZ')
    if not name:
        path = str(Path('/etc/localtime').resolve())
        if '/zoneinfo/' in path:
            name = path.split('/zoneinfo/', 1)[1]
    return ZoneInfo(name) if name else datetime.now().astimezone().tzinfo


def moment(value, zone):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.timestamp()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', choices=('both', 'claude', 'codex'), default='both')
    parser.add_argument('--window', choices=('current', 'last', 'both'),
                        help='Quota window to report: current usage so far, last completed, or both (default: last)')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'reports')
    parser.add_argument('--home', type=Path, help='Home directory containing local app data; overrides environment defaults')
    parser.add_argument('--codex-home', type=Path, help='Overrides HOME/.codex and CODEX_HOME')
    parser.add_argument('--claude-home', type=Path, help='Overrides HOME/.claude and CLAUDE_CONFIG_DIR')
    parser.add_argument('--timezone', help='IANA timezone; defaults to the machine timezone')
    parser.add_argument('--claude-log-timezone', help='Timezone of Claude desktop log timestamps; defaults to the machine timezone')
    parser.add_argument('--as-of', help='ISO timestamp; defaults to now')
    parser.add_argument('--codex-window', nargs=2, metavar=('START', 'END'), help='Explicit ISO bounds when reset records are unavailable')
    parser.add_argument('--claude-window', nargs=2, metavar=('START', 'END'), help='Explicit ISO bounds when reset records are unavailable')
    parser.add_argument('--normalize-parallel-worktrees', action='store_true',
                        help='In Claude cost breakdowns, replace path components that name parallel checkouts, '
                             'such as /B0/ or the .Z of labs.Z/, with /.../')
    parser.add_argument('--rates', type=Path, default=Path(__file__).with_name('rates.json'), help='Versioned USD-per-million-token price catalog')
    args = parser.parse_args(argv)
    if args.window and (args.claude_window or args.codex_window):
        parser.error('--window cannot be combined with explicit --claude-window or --codex-window bounds')
    home = (args.home or Path.home()).expanduser()
    zone = ZoneInfo(args.timezone) if args.timezone else local_zone()
    log_zone = ZoneInfo(args.claude_log_timezone) if args.claude_log_timezone else local_zone()
    asof = moment(args.as_of, zone) if args.as_of else datetime.now(timezone.utc).timestamp()
    catalog = json.loads(args.rates.read_text())
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = output / 'reset-history.json'
    observations = json.loads(state.read_text()) if state.exists() else []
    services = ('claude', 'codex') if args.service == 'both' else (args.service,)
    reports, errors = [], []
    for service in services:
        print(f'Reading {service} history...', flush=True)
        try:
            if service == 'codex':
                root = args.codex_home or (home / '.codex' if args.home else Path(os.environ.get('CODEX_HOME', str(home / '.codex'))))
                data, fresh, index, calls = scan_codex(root.expanduser(), asof)
            else:
                root = args.claude_home or (home / '.claude' if args.home else Path(os.environ.get('CLAUDE_CONFIG_DIR', str(home / '.claude'))))
                fresh, titles = claude_logs(home / 'Library/Logs/Claude', log_zone, asof)
            scope = str(root.expanduser().resolve())
            if service == 'claude':
                scope += '|' + str(home.resolve())
            for observation in fresh:
                observation['scope'] = scope
            observations = compact_quotas(observations + fresh)
            override = getattr(args, service + '_window')
            selections = ('custom',) if override else ('last', 'current') if args.window == 'both' else (args.window or 'last',)
            for selection in selections:
                try:
                    if override:
                        start, end = (moment(s, zone) for s in override)
                        if not start < end <= asof:
                            raise ValueError('explicit window must have START < END <= as-of time')
                        window = {'start': start, 'end': end, 'selection': 'custom',
                                  'basis': 'explicit command-line bounds', 'evidence': []}
                    else:
                        window = select_window([q for q in observations if q.get('scope') == scope], service, asof, selection)
                    if service == 'codex':
                        records, names, diagnostics = codex_reader.read(data, window['start'], window['end'], index, calls)
                    else:
                        records, names, diagnostics = claude_reader.read(root.expanduser() / 'projects',
                            home / 'Library/Application Support/Claude/claude-code-sessions',
                            window['start'], window['end'], asof, titles)
                    breakdown = None
                    if service == 'claude':
                        breakdown = claude_context.attribute(records, catalog, home, root,
                                                             args.normalize_parallel_worktrees)
                    report = aggregate(service, records, names, window, catalog, diagnostics, breakdown)
                    directory = save_report(output, report, records, zone)
                    summary = report['summary']
                    reports.append({'service': service, 'window': selection, 'path': str(directory), 'total': summary['window_total']})
                    print(f"{service} {selection}: {iso(window['start'], zone)} to {iso(window['end'], zone)}")
                    if selection == 'current':
                        print(f"  Recorded reset: {iso(window['reset'], zone)}")
                    print(f"  ${summary['window_total']:,.2f} priced usage; {len(names)} sessions; {summary['unpriced_requests']} unpriced requests")
                    share = summary['subagent_percent_of_total']
                    if share is not None:
                        print(f"  Subagents: ${summary['subagent_total']:,.2f} ({share:.1f}% of total)")
                    print(f'  {directory / "index.html"}', flush=True)
                except (ValueError, OSError, KeyError, sqlite3.Error) as error:
                    errors.append(f'{service} {selection}: {error}')
                    print(errors[-1], file=sys.stderr)
            if service == 'codex':
                del data
        except (ValueError, OSError, KeyError, sqlite3.Error) as error:
            errors.append(f'{service}: {error}')
            print(errors[-1], file=sys.stderr)
    temp = state.with_suffix('.tmp')
    temp.write_text(json.dumps(observations, indent=2) + '\n')
    temp.replace(state)
    (output / 'latest.json').write_text(json.dumps({'as_of': iso(asof, zone), 'reports': reports, 'errors': errors}, indent=2) + '\n')
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
