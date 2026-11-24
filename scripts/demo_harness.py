"""Reproduce a three-step guidance loop offline, with explicit user confirmations."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.harness import Controller, Event
from livekit_vlm_agent.harness.fake import FakeBackend, FakeClock


def main():
    clock = FakeClock()
    controller = Controller("offline-demo", clock=clock)
    backend = FakeBackend(controller)
    backend.send(Event("user_turn", text="教我搭三个积木"))
    backend.decide({"action": "start_task", "mode": "guidance", "goal": "搭三个积木",
        "plan": [{"id": f"s{i}", "instruction": f"放第{i}个积木",
                  "completion_condition": f"第{i}个积木放好"} for i in range(1, 4)],
        "current_step_id": "s1", "speech_intent": "先放第一个积木"})
    backend.finish_speech()
    for i in range(1, 4):
        task = controller.state.task
        print(f"step={task.step.id} status={task.status} completed={len(task.completed)}")
        backend.send(Event("user_turn", text="好了", input_id=f"u{i}"))
        backend.decide({"action": "complete" if i == 3 else "advance",
            "current_step_id": f"s{i}", "speech_intent": "完成了" if i == 3 else "继续下一步",
            "assessment": {"result": "complete", "basis": "user_reported",
                           "evidence_ids": [f"u{i}"], "reason": "用户明确确认本步骤完成"}})
        backend.finish_speech()
    print(f"status={controller.state.task.status} completed={controller.state.task.completed}")
    assert controller.state.task.status == "completed"
    assert controller.state.pending is None


if __name__ == "__main__":
    main()
