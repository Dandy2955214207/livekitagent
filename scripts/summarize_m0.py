"""Derive an audit summary from all M0 attempts, retaining failures and extra turns."""

from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import statistics

from verify_m0 import expected_content

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / ".runtime/m0-results"


def main():
    paths = sorted(RESULTS.glob("*-report.json")) + sorted((RESULTS / "history").glob("*-report.json"))
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model": "qwen3.8-omni-flash-realtime", "region": "cn-beijing",
        "baseline_only": True, "all_acceptance_criteria_passed": False,
        "known_issues": ["semantic_vad short-question transcription loss", "semantic_vad_ec splits question and trailing instruction"],
        "runs": [], "observed_terminal_responses": 0, "cancelled_responses": 0,
        "known_total_tokens_lower_bound": 0, "responses_with_usage": 0,
        "pause_comparisons": [], "first_audio_samples_ms": [],
        "dependency_versions": {name: version(name) for name in ("livekit-agents", "livekit", "livekit-api", "websockets")},
    }
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        trace_path = path.with_name(path.name.replace("-report.json", "-events.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        done = [event for event in trace if event["type"] == "response.done"]
        mode = report.get("turn_detection") or report.get("pause_mode") or (
            "semantic_vad" if report["stage"] in {"semantic", "controls", "pause"} else "manual")
        summary["runs"].append({"file": str(path.relative_to(ROOT)), "mode": mode,
            "stage": report["stage"], "terminal_responses": len(done),
            "execution_status": report["status"]})
        summary["observed_terminal_responses"] += len(done)
        summary["cancelled_responses"] += sum(e.get("status") in {"cancelled", "canceled"} for e in done)
        for case in report["cases"]:
            usage = case.get("usage") or {}
            if "total_tokens" in usage:
                summary["known_total_tokens_lower_bound"] += usage["total_tokens"]
                summary["responses_with_usage"] += 1
            name = case["case"]
            latency = case.get("first_audio_after_input_ms")
            if name != "short_pause" and latency is not None and latency >= 0:
                summary["first_audio_samples_ms"].append(latency)
            if name == "short_pause":
                summary["pause_comparisons"].append({"mode": case.get("vad_mode") or mode,
                    "text": case["text"], "input_transcripts": case.get("input_transcripts"),
                    "observed_responses_in_run": len(done), "source": str(path.relative_to(ROOT))})
    samples = summary["first_audio_samples_ms"]
    summary["first_audio_median_ms"] = statistics.median(samples)
    summary["first_audio_range_ms"] = [min(samples), max(samples)]
    # Rescore the original dog answer with valid Chinese aliases, without rewriting raw reports.
    baseline = []
    for stage in ("manual", "vision"):
        report = json.loads((RESULTS / f"{stage}-report.json").read_text(encoding="utf-8"))
        baseline.extend({"case": c["case"], "text": c["text"],
            "content_matches": expected_content(c["case"], c["text"])} for c in report["cases"])
    summary["manual_and_vision_content_checks"] = baseline
    summary["fixture_manifest"] = json.loads((ROOT / ".runtime/m0-fixtures/manifest.json").read_text(encoding="utf-8"))
    (RESULTS / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for key in ("observed_terminal_responses", "cancelled_responses", "known_total_tokens_lower_bound",
                "responses_with_usage", "first_audio_median_ms", "first_audio_range_ms"):
        print(f"{key}: {summary[key]}")


if __name__ == "__main__":
    main()
