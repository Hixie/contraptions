"""Shared parsing, quota selection, pricing, and report output."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import ast
import csv
import json
import re
import sqlite3

WEEK = 7 * 24 * 3600
CONVENTION_SOURCE = 'https://github.com/Hixie/settings/tree/main/system-prompts/common-fabric'


def stamp(value, fallback=0):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else value
    if value:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    return fallback


def iso(value, zone=timezone.utc):
    return datetime.fromtimestamp(value, zone).isoformat()


def maximum(a, b):
    for k, v in b.items():
        if isinstance(v, dict):
            maximum(a.setdefault(k, {}), v)
        elif isinstance(v, (int, float)):
            a[k] = max(a.get(k, 0) or 0, v)
        elif v is not None:
            a[k] = v


def footer(text):
    lines = [s.strip().strip('*_') for s in (text or '').splitlines()
             if s.strip() and not re.fullmatch(r'</[\w-]+>', s.strip())]
    if not lines:
        return None
    line = lines[-1]
    version = re.search(r'0x[0-9a-fA-F]{2,}', line)
    if version and len(line) <= 700:
        prefix = line[:version.start()]
        prefix = re.sub(r'\[[^\]\n]*\]\([^)\n]*\)', '', prefix)
        emoji = r'[\U0001F000-\U0001FAFF\u2300-\u27FF]\ufe0f?'
        if not re.fullmatch(r'(?:[\w./-]+(?:\s*,\s*[\w./-]+)*:\s*)?' + emoji + r'(?:\s*(?:→|->)\s*' + emoji + r')*\s+', prefix):
            return None
    elif re.fullmatch(r'(?:[\w,/ -]+:\s*)?[\U0001F000-\U0001FAFF\u2300-\u27FF]\ufe0f?\s*(?:\[#.*)?', line):
        prefix = line.split('[#')[0]
    else:
        return None
    emojis = re.findall(r'[\U0001F000-\U0001FAFF\u2300-\u27FF](?:\ufe0f)?', prefix)
    emojis = [e.replace('\ufe0f', '') for e in emojis if e not in ('🎬', '⚠', '⚠️')]
    return emojis[-1] if emojis else None


def jsonlines(path):
    with path.open('rb') as fp:
        for line, raw in enumerate(fp, 1):
            if not raw.strip():
                continue
            try:
                yield line, json.loads(raw)
            except ValueError as error:
                # An actively appended final line can be incomplete.
                if not raw.endswith(b'\n'):
                    return
                raise ValueError(f'{path}:{line}: invalid JSON') from error


def compact_quotas(observations):
    by_reset = {}
    for q in observations:
        key = (q.get('scope'), q['service'], q['reset'], q['duration'])
        old = by_reset.get(key)
        if old is None or q['observed'] < old['observed']:
            by_reset[key] = q
    return sorted(by_reset.values(), key=lambda q: q['observed'])


def select_window(observations, service, asof, selection='last'):
    if selection not in ('last', 'current'):
        raise ValueError(f'unknown window selection: {selection}')
    groups = []
    for q in compact_quotas(observations):
        if q['service'] != service or q['observed'] > asof or q['reset'] <= q['observed']:
            continue
        anchor = q['reset'] - q['duration']
        if anchor > q['observed']:
            continue
        # Minute-resolution window lengths produce small deadline variations.
        if not groups or anchor > max(groups[-1]['first_observed'], groups[-1]['start'] + 60):
            groups.append({'start': anchor, 'reset': q['reset'],
                           'first_observed': q['observed'], 'evidence': [q]})
        elif anchor >= groups[-1]['start'] - 60:
            # Keep the earliest opening anchor within the deadline cluster.
            groups[-1]['evidence'].append(q)
            groups[-1]['start'] = min(groups[-1]['start'], anchor)
            groups[-1]['reset'] = min(groups[-1]['reset'], q['reset'])
    candidates = []
    for i, group in enumerate(groups):
        following = groups[i + 1]['start'] if i + 1 < len(groups) else float('inf')
        end = min(group['reset'], following)
        eligible = group['start'] < end <= asof if selection == 'last' else group['start'] <= asof < end
        if eligible:
            candidates.append({'start': group['start'], 'end': min(end, asof),
                               'reset': end, 'selection': selection,
                               'basis': 'recorded weekly deadline and subsequent reset',
                               'evidence': group['evidence'] + (groups[i + 1]['evidence'][:1] if i + 1 < len(groups) else [])})
    if not candidates:
        kind = 'completed' if selection == 'last' else 'current'
        raise ValueError(f'{service}: no {kind} weekly window in the retained reset history; provide --{service}-window START END')
    return max(candidates, key=lambda w: w['reset'])


def previous_window(observations, service, asof):
    return select_window(observations, service, asof, 'last')


def percent(value, total):
    return 100 * value / total if total else None


def claude_reset(text, observed, zone):
    match = re.search(r"weekly limit.*?resets\s+(?:(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]m)\s*\((?P<zone>[^)]+)\)", text)
    if not match:
        return None
    local = datetime.fromtimestamp(observed, ZoneInfo(match['zone']) if match['zone'] else zone)
    hour = int(match['hour']) % 12 + (12 if match['ampm'] == 'pm' else 0)
    deadline = local.replace(hour=hour, minute=int(match['minute'] or 0), second=0, microsecond=0)
    if match['month']:
        month = datetime.strptime(match['month'], '%b').month
        deadline = deadline.replace(month=month, day=int(match['day']))
        if deadline <= local:
            deadline = deadline.replace(year=deadline.year + 1)
    elif deadline <= local:
        deadline += timedelta(days=1)
    return deadline.timestamp()


def claude_logs(directory, zone, asof):
    quotas, titles = [], []
    title_re = re.compile(r"Updated session ([\w-]+): \{ title: (.*), titleSource: '(user|auto|tool)' \}")
    for path in sorted(directory.glob('main*.log')):
        for line_number, line in enumerate(path.open(errors='replace'), 1):
            if 'weekly limit' not in line and 'Updated session ' not in line:
                continue
            try:
                t = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=zone).timestamp()
            except ValueError:
                continue
            if t > asof:
                continue
            reset = claude_reset(line, t, zone)
            if reset:
                quotas.append({'service': 'claude', 'observed': t, 'reset': reset, 'duration': WEEK,
                               'source': str(path), 'line': line_number, 'kind': 'weekly limit deadline'})
            m = title_re.search(line)
            if m:
                title = m[2].strip('"\'')
                titles.append((t, m[1], title, m[3]))
    return quotas, titles


def title_call(name, args, owner):
    if isinstance(args, dict):
        objects = [args] if 'set_thread_title' in name else []
    else:
        objects = []
        if 'set_thread_title' in name:
            try:
                objects.append(json.loads(args))
            except ValueError:
                pass
        # Treat quoted strings as whole tokens, including braces in a title.
        object_literal = r'''\{(?:[^{}"'`]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')*\}'''
        for match in re.finditer(r'set_thread_title\s*\(\s*(' + object_literal + ')', args):
            obj = {}
            for key, literal in re.findall(r'''["']?(title|threadId)["']?\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''', match[1]):
                try:
                    obj[key] = ast.literal_eval(literal)
                except (SyntaxError, ValueError):
                    pass
            objects.append(obj)
    return [(obj.get('threadId') or owner, obj['title']) for obj in objects if isinstance(obj.get('title'), str)]


def scan_codex(root, asof):
    metadata = {}
    paths = set((root / 'sessions').rglob('*.jsonl')) | set((root / 'archived_sessions').rglob('*.jsonl'))
    databases = set(root.glob('state_*.sqlite')) | set((root / 'sqlite').glob('state_*.sqlite'))
    for path in sorted(databases, key=lambda p: p.stat().st_mtime):
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            cols = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
            if not cols:
                continue
            wanted = ['id', 'rollout_path', 'created_at', 'thread_source', 'source', 'name', 'model']
            for row in db.execute('SELECT ' + ','.join(k for k in wanted if k in cols) + ' FROM threads'):
                m = dict(row)
                metadata[m['id']] = m
                if m.get('rollout_path') and Path(m['rollout_path']).is_file():
                    paths.add(Path(m['rollout_path']))
        finally:
            db.close()
    if not paths:
        raise ValueError(f'no Codex session files found in {root}')
    files, quotas, title_calls = [], [], defaultdict(list)
    for path in sorted(paths):
        fid = path.stem[-36:]
        events, meta, turn_start = [], {}, 0
        for line, x in jsonlines(path):
            t = stamp(x.get('timestamp'))
            if t > asof:
                continue
            k, p = x.get('type'), x.get('payload') or {}
            if k == 'session_meta':
                meta = {key: p.get(key) for key in ('id', 'session_id', 'parent_thread_id', 'source', 'thread_source', 'forked_from_id')}
                meta['timestamp'] = stamp(p.get('timestamp'))
                metadata.setdefault(fid, {'id': fid, 'model': None, 'thread_source': p.get('thread_source') or ('subagent' if isinstance(p.get('source'), dict) else 'user')})
                continue
            if k == 'turn_context':
                p = {key: p.get(key) for key in ('turn_id', 'model', 'service_tier')}
            elif k == 'token_usage_record':
                p = {key: p.get(key) for key in ('thread_id', 'session_id', 'turn_id', 'root_turn_id', 'response_id', 'usage')}
            elif k == 'event_msg':
                k = p.get('type')
                if k == 'task_started':
                    turn_start = stamp(p.get('started_at'), t)
                elif k == 'token_count':
                    limit = p.get('rate_limits') or {}
                    inherited = meta.get('forked_from_id') and turn_start < meta.get('timestamp', 0)
                    if limit.get('limit_id', 'codex') == 'codex' and not inherited:
                        for side in ('primary', 'secondary'):
                            w = limit.get(side) or {}
                            if w.get('window_minutes') == 10080 and w.get('resets_at'):
                                quotas.append({'service': 'codex', 'observed': t, 'reset': w['resets_at'], 'duration': WEEK,
                                               'source': str(path), 'line': line, 'kind': 'codex ' + side, 'turn_start': turn_start})
                    p = p.get('info')
                elif k == 'thread_settings_applied':
                    settings = p.get('thread_settings') or {}
                    p = {key: settings.get(key) for key in ('model', 'service_tier')}
                elif k not in ('task_complete', 'turn_aborted'):
                    continue
                if isinstance(p, dict) and 'last_agent_message' in p:
                    p['last_agent_message'] = (p['last_agent_message'] or '')[-1800:]
            elif k == 'response_item':
                sub = p.get('type')
                if sub == 'message' and p.get('role') == 'assistant' and p.get('phase') == 'final':
                    text = '\n'.join(b.get('text', '') for b in p.get('content', []) if isinstance(b, dict))
                    k, p = 'assistant', {'phase': 'final', 'text': text[-1800:], 'turn_id': (p.get('internal_chat_message_metadata_passthrough') or {}).get('turn_id')}
                elif sub in ('function_call', 'custom_tool_call'):
                    if not meta.get('forked_from_id') or turn_start >= meta.get('timestamp', 0):
                        for target, title in title_call(p.get('name', ''), p.get('arguments', p.get('input', '')), fid):
                            title_calls[target].append((t, title))
                    continue
                else:
                    continue
            else:
                continue
            events.append({'t': t, 'o': x.get('ordinal', line), 'k': k, 'p': p})
        files.append({'path': str(path), 'meta': meta, 'events': events})
    index = defaultdict(list)
    if (root / 'session_index.jsonl').exists():
        for _, e in jsonlines(root / 'session_index.jsonl'):
            if stamp(e.get('updated_at')) <= asof:
                index[e['id']].append((stamp(e.get('updated_at')), e['thread_name']))
    return {'metadata': list(metadata.values()), 'files': files}, quotas, index, title_calls


def codex_titles(active, index, filemeta, metadata, calls, first_turn):
    names = {}
    for sid in active:
        history = sorted(index.get(sid, []))
        tools = calls.get(sid, [])
        combined = sorted(history + tools)
        gears = [title for _, title in combined if '⚙' in title]
        if gears:
            names[sid] = (gears[0], 'first_gear')
            continue
        tool_names = {title for _, title in tools}
        unmatched = [(t, title) for t, title in history if title not in tool_names]
        fork = filemeta.get(sid, {}).get('forked_from_id')
        if fork:
            user_candidates = [(t, title) for t, title in unmatched if t >= first_turn.get(sid, float('inf'))]
            names[sid] = (user_candidates[0][1], 'first_user_inferred') if user_candidates else (history[0][1], 'original_fork_title') if history else (metadata.get(sid, {}).get('name') or sid, 'original_fork_title')
        elif unmatched:
            # The first name is supplied by the automatic title service.
            names[sid] = (unmatched[1][1], 'first_user_inferred') if len(unmatched) > 1 else (unmatched[0][1], 'last_auto')
        else:
            names[sid] = (history[0][1] if history else metadata.get(sid, {}).get('name') or sid, 'original_assigned_title')
    return names


def model_rates(service, model, catalog):
    models = catalog['services'][service]['models']
    rates = models.get(model)
    if rates is not None and 'alias' in rates:
        rates = models[rates['alias']]
    return rates


def claude_unit_prices(record, catalog):
    """USD per token for each part of a Claude request, or None if unpriced."""
    rates = model_rates('claude', record['model'], catalog)
    if rates is None:
        return None
    usage = record['usage']
    creation = usage.get('cache_creation_input_tokens', 0) or 0
    cache = usage.get('cache_creation') or {}
    one = cache.get('ephemeral_1h_input_tokens', 0) or 0
    five = cache.get('ephemeral_5m_input_tokens', 0) or 0
    if not one + five:
        five = creation
    elif one + five != creation:
        raise ValueError(f"{record['response_id']}: inconsistent Claude cache token counts")
    write = (five * rates['write_5m'] + one * rates['write_1h']) / (five + one) if five + one else rates['write_5m']
    multiplier = rates.get('fast_multiplier', 2) if usage.get('speed') == 'fast' else 1
    multiplier *= 1.1 if usage.get('inference_geo') == 'us' else 1
    return {'input': rates['input'] * multiplier / 1e6, 'read': rates['cached'] * multiplier / 1e6,
            'write': write * multiplier / 1e6, 'output': rates['output'] * multiplier / 1e6}


def price(service, record, catalog):
    if service == 'claude':
        unit = claude_unit_prices(record, catalog)
        if unit is None:
            return None
        usage = record['usage']
        value = sum((usage.get(k, 0) or 0) * unit[p] for k, p in (
            ('input_tokens', 'input'), ('cache_read_input_tokens', 'read'),
            ('cache_creation_input_tokens', 'write'), ('output_tokens', 'output')))
        return value + ((usage.get('server_tool_use') or {}).get('web_search_requests') or 0) * .01
    rates = model_rates(service, record['model'], catalog)
    if rates is None:
        return None
    usage = record['usage']
    i, cached, write, out = [usage.get(k, 0) or 0 for k in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens')]
    if i < cached + write:
        raise ValueError(f"{record['response_id']}: cached input exceeds total input")
    long = bool(rates.get('long_context_threshold') and i > rates['long_context_threshold'])
    value = ((i - cached - write) * rates['input'] + cached * rates['cached'] + write * rates['write']) * (2 if long else 1)
    value += out * rates['output'] * (1.5 if long else 1)
    multiplier = rates.get('fast_multiplier', 2) if record.get('tier') in ('priority', 'fast') else .5 if record.get('tier') in ('batch', 'flex') else 1
    return value / 1e6 * multiplier


def aggregate(service, records, names, window, catalog, diagnostics, breakdown=None):
    sessions = {sid: {'id': sid, 'title': title, 'title_rule': rule, 'window': 0,
                     'subagent_window': 0, 'segments': Counter(), 'models': Counter(),
                     'requests': 0, 'unpriced_requests': 0, 'unpriced_tokens': Counter()}
                for sid, (title, rule) in names.items()}
    unpriced_models = Counter()
    for r in records:
        s = sessions[r['session_id']]
        value = price(service, r, catalog)
        r['cost_usd'] = value
        if value is None:
            s['unpriced_requests'] += 1
            unpriced_models[r['model']] += 1
            s['unpriced_tokens'].update({k: v for k, v in r['usage'].items() if isinstance(v, (int, float))})
        else:
            s['window'] += value
            s['segments'][r['footer']] += value
            s['models'][r['model']] += value
            s['requests'] += 1
            if r['subagent']:
                s['subagent_window'] += value
    ordered = sorted(sessions.values(), key=lambda s: (-s['window'], s['title']))
    totals = Counter()
    for s in ordered:
        totals.update(s['segments'])
    total = sum(s['window'] for s in ordered)
    subagents = sum(s['subagent_window'] for s in ordered)
    for s in ordered:
        if breakdown:
            parts = breakdown.get(s['id'], {})
            s['breakdown'] = {lens: dict(rollup(parts.get(lens, {})).most_common()) for lens in LENSES}
        s['percent_of_total'] = percent(s['window'], total)
        s['subagent_percent_of_session'] = percent(s['subagent_window'], s['window'])
        s['segment_percent_of_session'] = {state: percent(value, s['window']) for state, value in s['segments'].items()}
    report = {'service': service, 'window': window, 'rates_checked': catalog['checked'],
            'price_source': catalog['services'][service]['source'],
            'convention_source': CONVENTION_SOURCE, 'sessions': ordered,
            'summary': {'session_count': len(ordered), 'window_total': total,
                        'segments': totals, 'subagent_total': subagents,
                        'segment_percent_of_total': {state: percent(value, total) for state, value in totals.items()},
                        'subagent_percent_of_total': percent(subagents, total),
                        'priced_requests': sum(s['requests'] for s in ordered),
                        'unpriced_requests': sum(s['unpriced_requests'] for s in ordered),
                        'unpriced_models': unpriced_models, 'diagnostics': diagnostics}}
    if breakdown:
        report['summary']['breakdown'] = summarize_breakdown(breakdown, total)
    return report


LENSES = ('context', 'actions')
# For each action: the requests that took it, those that took no other
# action, and the cost of the latter.
ACTION_DETAILS = ('requests', 'sole_requests', 'sole_cost')


def rollup(costs):
    categories = Counter()
    for (category, _), value in costs.items():
        categories[category] += value
    return categories


def summarize_breakdown(breakdown, total):
    result = {}
    for lens in LENSES:
        costs, sessions, extra = Counter(), defaultdict(set), defaultdict(Counter)
        for sid, parts in breakdown.items():
            for key, value in parts[lens].items():
                costs[key] += value
                sessions[key].add(sid)
            for name in ACTION_DETAILS if lens == 'actions' else ():
                extra[name].update(parts[name])
        categories = []
        for category, value in rollup(costs).most_common():
            keys = [k for k in costs if k[0] == category]
            items = [{'label': k[1], 'cost': costs[k], 'percent_of_total': percent(costs[k], total),
                      'sessions': len(sessions[k]), **{name: extra[name][k] for name in extra}}
                     for k in sorted(keys, key=lambda k: (-costs[k], k[1]))]
            categories.append({'category': category, 'cost': value, 'percent_of_total': percent(value, total),
                               'sessions': len(set().union(*(sessions[k] for k in keys))), 'items': items})
        lens_total = sum(costs.values())
        result[lens] = {'total': lens_total, 'percent_of_total': percent(lens_total, total), 'categories': categories}
    return result


def spreadsheet_text(value):
    # Prefix spreadsheet formulas while preserving text in JSON and HTML.
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def save_report(output, report, records, zone):
    service = report['service']
    window = report['window']
    current = window.get('selection') == 'current'
    closing = window['reset'] if current else window['end']
    suffix = '-current' if current else '-custom' if window.get('selection') == 'custom' else ''
    directory = output / (service + '-' + datetime.fromtimestamp(closing, zone).strftime('%Y%m%dT%H%M%S') + suffix)
    directory.mkdir(parents=True, exist_ok=True)
    report['window']['local_start'] = iso(report['window']['start'], zone)
    report['window']['local_end'] = iso(report['window']['end'], zone)
    if 'reset' in window:
        window['local_reset'] = iso(window['reset'], zone)
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with (directory / 'requests.jsonl').open('w') as fp:
        for record in records:
            fp.write(json.dumps(record, ensure_ascii=False) + '\n')
    states = list(report['summary']['segments'])
    fields = ['title', 'id', 'title_rule', 'window', 'percent_of_total', 'subagent_window',
              'subagent_percent_of_session', 'requests', 'unpriced_requests',
              'unpriced_input_tokens', 'unpriced_output_tokens'] + states + [s + '_percent_of_session' for s in states]
    with (directory / 'sessions.csv').open('w', newline='') as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for s in report['sessions']:
            row = {k: s[k] for k in fields if k in s}
            row.update({k: s['segments'].get(k, 0) for k in states})
            row.update({k + '_percent_of_session': percent(s['segments'].get(k, 0), s['window']) for k in states})
            row.update(unpriced_input_tokens=s['unpriced_tokens'].get('input_tokens', 0), unpriced_output_tokens=s['unpriced_tokens'].get('output_tokens', 0))
            row['title'] = spreadsheet_text(row['title'])
            writer.writerow(row)
    breakdown = report['summary'].get('breakdown')
    if breakdown:
        with (directory / 'breakdown.csv').open('w', newline='') as fp:
            writer = csv.writer(fp)
            writer.writerow(['lens', 'category', 'label', 'cost', 'percent_of_total', 'sessions', *ACTION_DETAILS])
            for lens in LENSES:
                for category in breakdown[lens]['categories']:
                    for item in category['items']:
                        writer.writerow([lens, spreadsheet_text(category['category']), spreadsheet_text(item['label']),
                                         item['cost'], item['percent_of_total'], item['sessions'],
                                         *(item.get(name, '') for name in ACTION_DETAILS)])
    template = Path(__file__).with_name('report.html').read_text()
    data = json.dumps(report, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    (directory / 'index.html').write_text(template.replace('REPORT_DATA', data))
    return directory
