"""Read Codex response usage and legacy token notifications."""
from pathlib import Path
from reporting import stamp, footer, codex_titles

FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def vector(usage):
    return tuple((usage or {}).get(k, 0) or 0 for k in FIELDS)


def read(x, start, end, index, title_calls):
    START, END = start, end
    metadata = {m['id']: m for m in x['metadata']}
    filemeta = {Path(f['path']).stem[-36:]: f['meta'] for f in x['files']}
    turns = {}
    modern = {}
    legacy = {}
    legacy_repeated = 0
    modern_copies = 0

    for f in x['files']:
        fid = Path(f['path']).stem[-36:]
        meta = f['meta']
        model = None
        tier = 'default'
        tid = 'legacy:' + fid
        previous_count = None
        last_modern = None
        for e in f['events']:
            k, p, t, ordinal = e['k'], e['p'], e['t'], e['o']
            if k == 'task_started':
                tid = p.get('turn_id') or 'legacy:' + fid + ':' + str(ordinal)
                start = stamp(p.get('started_at'), t)
                turn = turns.setdefault(tid, {'id': tid, 'start': start, 'files': []})
                if start < turn['start']:
                    turn['start'] = start
                if fid not in turn['files']:
                    turn['files'].append(fid)
            elif k == 'turn_context':
                tid = p.get('turn_id') or tid
                model = p.get('model') or model
                tier = p.get('service_tier') or tier
                turn = turns.setdefault(tid, {'id': tid, 'start': t, 'files': [fid]})
                turn['model'] = model
                turn['tier'] = tier
            elif k == 'thread_settings_applied':
                model = p.get('model') or model
                tier = p.get('service_tier') or tier
            elif k in ('task_complete', 'turn_aborted'):
                ct = p.get('turn_id') or tid
                turn = turns.setdefault(ct, {'id': ct, 'start': t, 'files': [fid]})
                turn['end'] = stamp(p.get('completed_at'), t)
                tail = p.get('last_agent_message') or ''
                if tail:
                    turn['final'] = tail
                    turn['footer'] = footer(tail)
            elif k == 'assistant' and p.get('phase') == 'final':
                ct = p.get('turn_id') or tid
                turn = turns.setdefault(ct, {'id': ct, 'start': t, 'files': [fid]})
                if p.get('text'):
                    turn['final'] = p['text']
                    turn['footer'] = footer(p['text'])
            elif k == 'token_usage_record':
                rid = p['response_id']
                owner = p['thread_id']
                record = {'response_id': rid, 'thread_id': owner, 'session_id': p['session_id'],
                          'turn_id': p['turn_id'], 'root_turn_id': p.get('root_turn_id'),
                          'timestamp': t, 'model': model, 'tier': tier, 'usage': p['usage'],
                          'path': f['path'], 'ordinal': ordinal, 'format': 'token_usage_record'}
                last_modern = (p['turn_id'], vector(p['usage']), rid)
                if rid in modern:
                    modern_copies += 1
                    if vector(modern[rid]['usage']) != vector(p['usage']):
                        raise ValueError(f'inconsistent usage for response {rid}')
                if rid not in modern or t < modern[rid]['timestamp']:
                    modern[rid] = record
                turn = turns.setdefault(p['turn_id'], {'id': p['turn_id'], 'start': t, 'files': [fid]})
                turn['thread_id'] = owner
                turn['session_id'] = p['session_id']
                turn['root_turn_id'] = p.get('root_turn_id')
            elif k == 'token_count' and p:
                total, last = vector(p.get('total_token_usage')), vector(p.get('last_token_usage'))
                # Total-only notifications are context-size estimates, not usage.
                if total == previous_count or not any(last[:4]):
                    legacy_repeated += 1
                    continue
                previous_count = total
                key = (tid or fid, total, last)
                match = last_modern[2] if last_modern and last_modern[:2] == (tid, last) else None
                if match:
                    last_modern = None
                old = legacy.get(key)
                record = {'response_id': 'legacy:' + fid + ':' + str(ordinal),
                          'thread_id': fid, 'session_id': meta.get('session_id') or fid,
                          'turn_id': tid, 'root_turn_id': None, 'timestamp': t,
                          'model': model, 'tier': tier, 'usage': dict(zip(FIELDS, last)),
                          'path': f['path'], 'ordinal': ordinal, 'format': 'token_count',
                          'matched_response_id': match}
                if old and old.get('matched_response_id'):
                    record['matched_response_id'] = old['matched_response_id']
                if old is None or t < old['timestamp']:
                    legacy[key] = record
                elif match:
                    old['matched_response_id'] = match

    # Resolve the occasional compaction record written before its context event.
    for r in modern.values():
        r['model'] = r['model'] or turns.get(r['turn_id'], {}).get('model') or metadata.get(r['thread_id'], {}).get('model')

    fallback = []
    for r in legacy.values():
        if r['matched_response_id']:
            continue
        turn = turns.get(r['turn_id'], {})
        # Copied history retains its original turn completion time.
        if turn.get('end', r['timestamp']) < START and r['timestamp'] >= START:
            continue
        if START <= r['timestamp'] < END:
            fallback.append(r)

    records = [r for r in modern.values() if START <= r['timestamp'] < END] + fallback

    # Assign real subagents to their enclosing user-owned session. User-created
    # forks stay separate; copied history was removed at request level above.
    def root_for(r):
        owner = r['thread_id']
        if metadata.get(owner, {}).get('thread_source') in ('user', 'agent_created_thread'):
            return owner
        sid = r.get('session_id')
        if metadata.get(sid, {}).get('thread_source') in ('user', 'agent_created_thread'):
            return sid
        seen = set()
        while owner not in seen:
            seen.add(owner)
            parent = filemeta.get(owner, {}).get('parent_thread_id')
            if not parent:
                return sid or owner
            owner = parent
            if metadata.get(owner, {}).get('thread_source') in ('user', 'agent_created_thread'):
                return owner
        raise ValueError('Cyclic parent lineage')


    for r in records:
        root = root_for(r)
        own = turns.get(r['turn_id'], {}).get('footer')
        parent = turns.get(r.get('root_turn_id'), {}).get('footer')
        r['subagent'] = r['thread_id'] != root
        r['session_id'] = root
        r['footer'] = own or (parent if r['subagent'] else None) or 'none'
        r['footer_source'] = 'own' if own else 'parent' if parent and r['subagent'] else 'absent'
    first_usage = {}
    for r in modern.values():
        first_usage[r['turn_id']] = min(first_usage.get(r['turn_id'], r['timestamp']), r['timestamp'])
    active = {r['session_id'] for r in records}
    first_turn = {}
    for t in turns.values():
        for fid in t['files']:
            created = filemeta.get(fid, {}).get('timestamp', 0)
            if metadata.get(fid, {}).get('thread_source') in ('user', 'agent_created_thread') and created <= t['start']:
                first_turn[fid] = min(first_turn.get(fid, t['start']), t['start'])
                if START <= t['start'] < END and first_usage.get(t['id'], 0) < END:
                    active.add(fid)
    names = codex_titles(active, index, filemeta, metadata, title_calls, first_turn)
    return records, names, {'modern_unique': len(modern), 'modern_copies': modern_copies,
        'legacy_requests_in_window': len(fallback), 'repeated_notifications': legacy_repeated}
