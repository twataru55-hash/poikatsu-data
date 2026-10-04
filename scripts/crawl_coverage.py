"""Cumulative, time-stamped crawl coverage across partial runs."""
from common import WORK_DIR, load_json, save_json


def record_coverage(queue, sources):
    audits = {a['source_id']: a for a in queue['coverage']}
    failed = {a['source_id']: a for a in queue['failed']}
    measured = {}
    for src in sources:
        sid = src['id']
        items = [i for i in queue['items'] if i.get('parent', i['source_id']) == sid]
        def belongs(value):
            return value == sid or value.startswith(sid + '--')
        audit = audits.get(sid)
        if not audit and sid not in failed and not items and sid not in queue['unchanged'] and sid not in queue['gave_up']:
            continue  # e.g. weekly source not visited today
        measured[sid] = {
            'run_id': queue['run_id'], 'measured_at': queue['created_at'],
            'listing_ok': sid not in failed, 'listing_error': failed.get(sid, {}).get('error'),
            'discovered': len((audit or {}).get('discovered', [])),
            'unvisited': len((audit or {}).get('unvisited', [])),
            'excluded': len((audit or {}).get('excluded', [])),
            'detail_failed': sum(belongs(x['source_id']) for x in queue['detail_failed']),
            'requested': len(items), 'deferred': sum(belongs(x) for x in queue['deferred']),
            'gave_up': sum(belongs(x) for x in queue['gave_up']),
            'complete_claim': False,
        }
    path = WORK_DIR / 'discovery' / 'coverage-summary.json'
    old = load_json(path, {}).get('sources', {})
    old.update(measured)
    result = {'latest_run_id': queue['run_id'], 'sources': old,
              'note': '取得元ごとのmeasured_atを確認。全市場網羅・画像内容の確認済みを意味しない。'}
    save_json(path, result)
    queue['source_summary'] = measured
    return result
