"""Lexical local snapshots, bounded and explicitly labelled as quoted history."""

import json
import re
import time


def terms(text):
    words = set(re.findall(r'[a-z0-9_]+', text.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(run[i:i+2] for i in range(max(1, len(run)-1)))
    return words


def snapshot(store, query='', *, max_items=8, max_chars=3000):
    if not store.enabled:
        return {'status': 'disabled', 'records': [], 'quoted_context': ''}
    now = time.time()
    wanted = terms(query)
    records = [r for r in store.records() if r.validity in {'current', 'historical'}
               and (r.expires_at is None or r.expires_at > now)]
    ranked = sorted(records, key=lambda r: (len(terms(r.content) & wanted), r.updated_at), reverse=True)
    selected = []
    for r in ranked:
        if wanted and not wanted & terms(r.content):
            continue
        item = {'memory_id': r.memory_id, 'kind': r.kind, 'content': r.content,
                'source': r.source, 'source_ids': r.source_ids, 'validity': r.validity,
                'observed_at': r.observed_at}
        candidate = json.dumps(selected + [item], ensure_ascii=False)
        if len(candidate) > max_chars:
            continue
        selected.append(item)
        if len(selected) >= max_items:
            break
    quoted = json.dumps(selected, ensure_ascii=False) if selected else ''
    return {'status': 'local_only', 'records': selected, 'quoted_context': quoted,
            'remote_sync': 'not_connected'}
