from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.providers.qwen_control import FunctionCallCollector


class CollectorTests(unittest.TestCase):
    def item(self, call_id="c1", arguments="{}"):
        return {"type": "response.output_item.done", "response_id": "r1", "item": {
            "type": "function_call", "call_id": call_id, "name": "submit_task_decision",
            "arguments": arguments}}

    def done(self, status="completed"):
        return {"type": "response.done", "response": {"id": "r1", "status": status}}

    def test_deltas_and_argument_done_are_not_executable(self):
        collector = FunctionCallCollector("r1")
        for kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
            self.assertIsNone(collector.feed({"type": kind, "response_id": "r1",
                                             "arguments": "{}", "call_id": "c1"}))
        self.assertIsNone(collector.feed(self.item()))
        self.assertEqual(collector.feed(self.done())[0].id, "c1")
        self.assertIsNone(collector.feed(self.done()))

    def test_cancelled_failed_and_truncated_results_are_empty(self):
        for status in ("cancelled", "failed", "incomplete"):
            collector = FunctionCallCollector("r1")
            collector.feed(self.item())
            self.assertEqual(collector.feed(self.done(status)), ())

    def test_foreign_response_and_partial_arguments_never_execute(self):
        collector = FunctionCallCollector("r2")
        self.assertIsNone(collector.feed(self.item()))
        self.assertIsNone(collector.feed(self.done()))
        collector = FunctionCallCollector("r1")
        self.assertEqual(collector.feed(self.done()), ())

    def test_multiple_complete_calls_are_collected_as_one_batch(self):
        collector = FunctionCallCollector("r1")
        collector.feed(self.item())
        collector.feed(self.item("c2"))
        self.assertEqual(len(collector.feed(self.done())), 2)

    def test_conflicting_duplicate_or_oversized_arguments_invalidate_batch(self):
        for arguments in ("{\"changed\":true}", "x" * 16001):
            collector = FunctionCallCollector("r1")
            collector.feed(self.item())
            collector.feed(self.item(arguments=arguments))
            self.assertEqual(collector.feed(self.done()), ())
