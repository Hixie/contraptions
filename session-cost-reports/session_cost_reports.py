#!/usr/bin/env python3
"""Generate local Claude and Codex reports for effective quota windows."""
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
                       detected_windows, select_window, save_report, scan_codex)


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


def print_detected(service, observations, account_activity, asof, zone):
    if account_activity:
        print(f'{service} observed account activity:')
        for activity in account_activity:
            account = activity['account'][:8]
            start = iso(activity['start'], zone)
            if activity['end'] is None:
                print(f'  account {account} (current): {start}; end unknown')
            else:
                print(f"  account {account}: {start} to {iso(activity['end'], zone)}")
    print(f'{service} detected quota windows:')
    windows = detected_windows(observations, service, asof)
    if not windows:
        print('  none')
    for window in windows:
        kinds = ', '.join(sorted({item.get('kind', 'quota deadline') for item in window['evidence']}))
        account = window.get('account')
        label = kinds + (f', account {account[:8]}' if account else '')
        print(f"  {label}: {iso(window['start'], zone)} to {iso(window['end'], zone)}")


def print_window(service, selection, window, zone):
    print(f'{service} {selection}:')
    if selection == 'custom':
        print(f"  specified: {iso(window['start'], zone)} to {iso(window['end'], zone)}")
        return
    print(f"  effective: {iso(window['start'], zone)} to {iso(window['end'], zone)}")
    if selection == 'current':
        print(f"  recorded reset: {iso(window['reset'], zone)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', choices=('both', 'claude', 'codex'), default='both')
    parser.add_argument('--window', choices=('current', 'last', 'both'),
                        help='Quota window to report: current usage so far, last completed, or both (default: last)')
    parser.add_argument('--print-windows', action='store_true',
                        help='Print detected, effective, or specified windows without generating reports')
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
    catalog = None if args.print_windows else json.loads(args.rates.read_text())
    output = args.output.expanduser().resolve()
    if not args.print_windows:
        output.mkdir(parents=True, exist_ok=True)
    state = output / 'reset-history.json'
    services = ('claude', 'codex') if args.service == 'both' else (args.service,)
    needs_history = not args.print_windows or any(
        getattr(args, service + '_window') is None for service in services)
    observations = json.loads(state.read_text()) if needs_history and state.exists() else []
    reports, errors = [], []
    for service in services:
        try:
            override = getattr(args, service + '_window')
            account_activity = []
            if args.print_windows and override:
                fresh = []
            else:
                print(f'Reading {service} history...', flush=True)
                if service == 'codex':
                    root = args.codex_home or (home / '.codex' if args.home else Path(os.environ.get('CODEX_HOME', str(home / '.codex'))))
                    data, fresh, index, calls = scan_codex(root.expanduser(), asof)
                else:
                    root = args.claude_home or (home / '.claude' if args.home else Path(os.environ.get('CLAUDE_CONFIG_DIR', str(home / '.claude'))))
                    fresh, titles, account_activity = claude_logs(home / 'Library/Logs/Claude', log_zone, asof)
                scope = str(root.expanduser().resolve())
                if service == 'claude':
                    scope += '|' + str(home.resolve())
                for observation in fresh:
                    observation['scope'] = scope
                observations = compact_quotas(observations + fresh)
            service_observations = [q for q in observations if q.get('scope') == scope] if not override else []
            if args.print_windows and not override:
                print_detected(service, service_observations, account_activity, asof, zone)
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
                        window = select_window(service_observations, service, asof, selection)
                    if args.print_windows:
                        print_window(service, selection, window, zone)
                        continue
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
                if not (args.print_windows and override):
                    del data
        except (ValueError, OSError, KeyError, sqlite3.Error) as error:
            errors.append(f'{service}: {error}')
            print(errors[-1], file=sys.stderr)
    if args.print_windows:
        return 1 if errors else 0
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
