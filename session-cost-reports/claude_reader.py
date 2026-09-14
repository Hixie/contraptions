"""Read Claude Code and Claude Desktop accounting records."""
from pathlib import Path
from collections import Counter, defaultdict
import json
from reporting import stamp, footer, maximum, jsonlines


def has_usage(usage):
    return any(has_usage(value) if isinstance(value, dict)
               else isinstance(value, (int, float)) and value > 0
               for value in usage.values())


def read(projects, desktop, start, end, asof, title_sources):
    PROJECTS, DESKTOP, START, END, ASOF = projects, desktop, start, end, asof
    metadata = {}
    aliases = {}
    source_events = defaultdict(list)
    for ts, sid, title, source in title_sources:
        source_events[sid].append((ts, source, title))
    for path in DESKTOP.rglob('*.json'):
        x = json.loads(path.read_text())
        if not x.get('sessionId'):
            continue
        sid = x['sessionId']
        metadata[sid] = {k: x.get(k) for k in [
            'sessionId', 'cliSessionId', 'priorCliSessionIds', 'createdAt',
            'lastActivityAt', 'title', 'titleSource', 'previousTitles', 'cwd',
            'spawnedFrom', 'forkedFromSessionId', 'forkedAtMessageUuid', 'error',
        ]}
        for cli in [x.get('cliSessionId'), *(x.get('priorCliSessionIds') or [])]:
            if cli:
                aliases[cli] = sid


    def canonical(cli):
        return aliases.get(cli, cli)


    def created(sid):
        return stamp(metadata.get(sid, {}).get('createdAt'))


    requests = {}
    user_events = {}
    title_events = defaultdict(list)
    tool_titles = defaultdict(list)
    tool_requests = {}
    agent_links = defaultdict(set)
    activity = defaultdict(list)
    source_paths = defaultdict(set)
    diagnostics = Counter()
    cli_models = Counter()
    paths = sorted(PROJECTS.rglob('*.jsonl'))
    if not paths:
        raise ValueError(f'no Claude session files found in {PROJECTS}')

    for path_index, path in enumerate(paths):
        rel = path.relative_to(PROJECTS).parts
        agent_file = 'subagents' in rel
        if agent_file:
            cli = rel[rel.index('subagents') - 1]
            file_agent = path.stem.removeprefix('agent-')
        else:
            cli = path.stem
            file_agent = None
        sid = canonical(cli)
        source_paths[sid].add(str(path))
        last_ts = 0
        for line_number, x in jsonlines(path):
            ts = stamp(x.get('timestamp'))
            last_ts = ts or last_ts
            if ts > ASOF:
                continue
            typ = x.get('type')
            m = x.get('message') or {}
            content = m.get('content') or []
            raw_sid = canonical(x.get('sessionId') or cli)
            agent = x.get('agentId') or file_agent
            if typ in ['user', 'assistant'] and ts:
                # Forked history can carry the child's session ID. Its timestamp
                # still precedes the fork's creation.
                if ts >= created(sid) - 2 or not metadata.get(sid, {}).get('forkedFromSessionId'):
                    activity[sid].append(ts)
            if typ in ['custom-title', 'ai-title'] and not agent_file:
                title = x.get('customTitle') or x.get('aiTitle')
                if title:
                    title_events[sid].append((last_ts, path_index, line_number, typ, title))
            if typ == 'user':
                result = x.get('toolUseResult')
                if isinstance(result, dict) and result.get('agentId'):
                    tool_id = x.get('sourceToolAssistantUUID')
                    if isinstance(content, list):
                        tool_id = next((b.get('tool_use_id') for b in content
                                        if isinstance(b, dict) and b.get('type') == 'tool_result'), tool_id)
                    if tool_id:
                        agent_links[(sid, result['agentId'])].add(tool_id)
                is_tool_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get('type') == 'tool_result' for b in content)
                if not is_tool_result and not x.get('isCompactSummary') and not x.get('isMeta') and ts:
                    uid = x.get('uuid') or f'{path}:{line_number}'
                    event = {'ts': ts, 'sid': sid, 'agent': agent, 'origin': x.get('origin'), 'prompt': x.get('promptId')}
                    old = user_events.get(uid)
                    if old is None or (created(sid) or ts, sid) < (created(old['sid']) or ts, old['sid']):
                        user_events[uid] = event
            if typ != 'assistant' or not ts:
                continue
            key = m.get('id')
            if not key or key == '<synthetic>':
                key = x.get('uuid') or f'{path}:{line_number}'
            model = m.get('model') or '<unknown>'
            cli_models[model] += 1
            r = requests.setdefault(key, {
                'id': key, 'request_id': x.get('requestId'), 'ts': ts, 'end': ts,
                'model': model, 'usage': {}, 'owners': set(), 'tail': '',
                'tail_ts': 0, 'stop': None, 'blocks': set(), 'agent': agent,
            })
            r['owners'].add((sid, agent or '', raw_sid))
            r['ts'] = min(r['ts'], ts)
            r['end'] = max(r['end'], ts)
            r['blocks'].add(x.get('uuid') or (path_index, line_number))
            maximum(r['usage'], m.get('usage') or {})
            if m.get('stop_reason'):
                r['stop'] = m['stop_reason']
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'text' and ts >= r['tail_ts']:
                    r['tail'] = block.get('text', '')[-2400:]
                    r['tail_ts'] = ts
                if block.get('type') == 'tool_use':
                    name = block.get('name', '')
                    inp = block.get('input') or {}
                    tool_requests[block.get('id')] = key
                    if 'set_session_title' in name and inp.get('title'):
                        target = inp.get('session_id')
                        target = sid if target in [None, 'self'] else canonical(target)
                        tool_titles[target].append((ts, inp['title']))
    streams = defaultdict(list)
    for r in requests.values():
        # A copied request belongs to the earliest session that contains it.
        # Prefer a session that existed when the request was made.
        owners = list(r.pop('owners'))
        owners.sort(key=lambda o: (
            created(o[0]) > r['ts'] + 2,
            created(o[0]) or r['ts'],
            o[0] != o[2],
            o[0], o[1],
        ))
        sid, agent, raw = owners[0]
        r['sid'] = sid
        r['agent'] = agent or None
        r['block_count'] = len(r.pop('blocks'))
        r['footer'] = footer(r['tail']) if r['stop'] in ['end_turn', 'stop_sequence'] else None
        if len({o[0] for o in owners}) > 1:
            diagnostics['requests_copied_across_sessions'] += 1
        streams[(sid, agent)].append((r['ts'], 1, r))
    for uid, u in user_events.items():
        streams[(u['sid'], u['agent'] or '')].append((u['ts'], 0, u))

    turns = []
    request_turn = {}
    for (sid, agent), events in streams.items():
        events.sort(key=lambda e: (e[0], e[1]))
        pending = []
        start = 0

        def finish(final=None):
            nonlocal pending, start
            if not pending:
                return
            idx = len(turns)
            turn = {'sid': sid, 'agent': agent or None, 'start': start or pending[0]['ts'],
                    'end': final['end'] if final else pending[-1]['end'],
                    'footer': final['footer'] if final else None,
                    'closed': bool(final), 'requests': [r['id'] for r in pending]}
            if final:
                turn['last_line'] = final['tail'].strip().splitlines()[-1:] or ['']
            turns.append(turn)
            for r in pending:
                request_turn[r['id']] = idx
            pending = []
            start = 0

        for ts, kind, obj in events:
            if kind == 0:
                # Messages arriving during an unfinished response can steer the same
                # turn. The final assistant message remains its observable boundary.
                start = start or ts
            else:
                pending.append(obj)
                start = start or ts
                if obj['stop'] in ['end_turn', 'stop_sequence', 'max_tokens']:
                    finish(obj)
        finish()


    def turn_state(idx, seen=None):
        turn = turns[idx]
        if turn['footer']:
            return turn['footer'], 'own_footer'
        if not turn['agent']:
            return 'none', 'no_footer'
        seen = set() if seen is None else seen
        if idx in seen:
            return 'none', 'no_footer'
        seen.add(idx)
        # Resuming an agent creates a new invocation in the same transcript.
        # Choose the latest invocation that had started when this turn began.
        parents = [tool_requests[tool_id]
                   for tool_id in agent_links.get((turn['sid'], turn['agent']), ())
                   if tool_id in tool_requests]
        parents = [key for key in parents if requests[key]['ts'] <= turn['start']]
        parent_request = max(parents, key=lambda key: requests[key]['ts'], default=None)
        parent_turn = request_turn.get(parent_request)
        if parent_turn is not None:
            state, source = turn_state(parent_turn, seen)
            if state != 'none':
                return state, 'parent_footer'
        return 'none', 'no_footer'



    def session_title(sid):
        meta = metadata.get(sid, {})
        origins = sorted(source_events.get(sid, []))
        if meta.get('forkedFromSessionId'):
            origins = [e for e in origins if e[0] >= created(sid) - 2]
        known_origins = [e for e in origins if e[2] and e[2] != '<redacted>']
        evs = sorted(set(title_events.get(sid, [])), key=lambda e: (e[0] or END, e[1], e[2]))
        if meta.get('forkedFromSessionId'):
            evs = [e for e in evs if e[0] >= created(sid) - 2]
        recorded_tools = tool_titles.get(sid, [])
        if meta.get('forkedFromSessionId'):
            recorded_tools = [e for e in recorded_tools if e[0] >= created(sid) - 2]
        titles = [(e[0], e[4], e[3]) for e in evs]
        titles += [(ts, title, 'tool') for ts, title in recorded_tools]
        titles += [(ts, title, source) for ts, source, title in known_origins]
        titles.sort(key=lambda e: e[0] or END)
        gears = [title for _, title, _ in titles if '⚙' in title]
        if gears:
            return gears[0], 'first_gear'
        historical = list(reversed(meta.get('previousTitles') or [])) + [meta.get('title')]
        if not meta.get('forkedFromSessionId'):
            gears = [t for t in historical if t and '⚙' in t]
            if gears:
                return gears[0], 'first_gear_metadata'
        tool_set = {t for _, t in recorded_tools} | {t for _, source, t in known_origins if source == 'tool'}
        auto_set = {e[4] for e in evs if e[3] == 'ai-title'}
        auto_set |= {t for _, source, t in known_origins if source == 'auto'}
        spawn_title = (meta.get('spawnedFrom') or {}).get('title')
        if meta.get('titleSource') == 'auto':
            auto_set.add(meta.get('title'))
        # Claude stores custom titles without their author. Tool calls and the
        # automatic-title records identify the titles that were not user edits.
        users = list(dict.fromkeys(e[4] for e in evs if e[3] == 'custom-title'
                 and e[4] not in tool_set and e[4] not in auto_set and e[4] != spawn_title))
        auto_changes = [e for e in origins if e[1] == 'auto']
        user_changes = [e for e in origins if e[1] == 'user']
        known_users = [t for _, source, t in known_origins if source == 'user']
        if known_users:
            return known_users[0], 'first_user_verified'
        if auto_changes and not user_changes and users:
            return users[-1], 'last_auto_desktop_log'
        if user_changes and auto_changes and users:
            earlier_auto_count = sum(e[0] < user_changes[0][0] for e in auto_changes)
            skip = min(earlier_auto_count, max(0, len(users) - len(user_changes)))
            users = users[skip:]
        if users:
            if meta.get('forkedFromSessionId'):
                forks = [t for t in historical if t and '(fork)' in t]
                forks += [e[4] for e in evs if '(fork)' in e[4]]
                if forks:
                    return forks[0], 'original_fork_title'
            return users[0], 'first_user_verified' if user_changes else 'first_user_inferred'
        if meta.get('titleSource') == 'user' and meta.get('title'):
            return next((t for t in historical if t and t not in tool_set and t not in auto_set), meta['title']), 'first_user_metadata'
        autos = [(ts, title) for ts, title, source in titles if source in ('ai-title', 'auto')]
        if meta.get('titleSource') == 'auto' and meta.get('title'):
            return meta['title'], 'last_auto_metadata'
        if autos:
            return max(autos, key=lambda e: e[0])[1], 'last_auto'
        return spawn_title or meta.get('title') or sid, 'original_assigned_title'

    normalized = []
    for r in requests.values():
        if not START <= r['end'] < END or not has_usage(r['usage']):
            continue
        state, source = turn_state(request_turn[r['id']])
        normalized.append({'response_id': r['id'], 'session_id': r['sid'],
            'timestamp': r['end'], 'model': r['model'], 'usage': r['usage'],
            'footer': state, 'footer_source': source, 'subagent': bool(r['agent']),
            'turn_id': str(request_turn[r['id']]), 'tier': r['usage'].get('speed', 'default')})
    active = {sid for sid, times in activity.items() if any(START <= t < END for t in times)}
    active |= {sid for sid, meta in metadata.items() if START <= stamp(meta.get('lastActivityAt')) < END}
    active |= {r['session_id'] for r in normalized}
    names = {sid: session_title(sid) for sid in active}
    return normalized, names, dict(diagnostics)
