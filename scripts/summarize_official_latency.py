"""Summarize local metadata-only timing logs; never reads transcripts or media.

Example: python scripts/summarize_official_latency.py .runtime/logs/worker.log
Playback ACK metrics include the return network trip, not acoustic latency.
Camera-to-clear is a server command measurement. Evidence age is not the time
the physical action actually finished. Small samples are descriptive only.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def summarize(paths):
    groups = defaultdict(list)
    for path in paths:
        for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
            if line.lstrip().startswith('{'):
                try:
                    line = json.loads(line).get('message', '')
                except (ValueError, AttributeError):
                    continue
            if 'official_latency ' not in line:
                continue
            try:
                item = json.loads(line.split('official_latency ', 1)[1])
                value = item['ms']
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 600000:
                    continue
                groups[(item['metric'], item.get('kind') or 'all')].append(value)
            except (ValueError, KeyError, TypeError):
                continue
    report = []
    for (metric, kind), values in sorted(groups.items()):
        values.sort()
        percentile = lambda p: round(values[max(0, math.ceil(len(values) * p) - 1)], 1)
        report.append(dict(metric=metric, kind=kind, count=len(values), p50_ms=percentile(.5),
                           p95_ms=percentile(.95), max_ms=round(values[-1], 1)))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps({'measurement': 'server_clock_and_browser_ack_not_acoustic',
                      'metrics': summarize(args.logs)}, indent=2, ensure_ascii=False))
