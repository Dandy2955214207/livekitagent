import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('latency_report', Path(__file__).resolve().parents[1] / 'scripts/summarize_official_latency.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LatencyReportTests(unittest.TestCase):
    def test_plain_and_livekit_json_logs_skip_bad_samples_and_keep_metric_groups(self):
        def sample(ms):
            return 'official_latency ' + json.dumps(dict(metric='trigger_to_playback_ack', kind='visual', ms=ms))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'worker.log'
            path.write_text('\n'.join([sample(100), json.dumps({'message': sample(300)}), sample(-1),
                                       sample(float('nan')), 'official_latency broken', 'unrelated log']), encoding='utf-8')
            result = module.summarize([path])
        self.assertEqual(result, [dict(metric='trigger_to_playback_ack', kind='visual', count=2,
                                       p50_ms=100, p95_ms=300, max_ms=300)])
