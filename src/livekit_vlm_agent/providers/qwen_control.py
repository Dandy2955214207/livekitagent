"""H0-only control-event assembly. This does not enable LiveKit tool support."""

from ..harness.types import ToolCall


class FunctionCallCollector:
    """Never expose parameter deltas or an unsuccessful response as a decision."""

    def __init__(self, response_id: str):
        self.response_id = response_id
        self.items = {}
        self.finished = False
        self.invalid = False

    def feed(self, event: dict) -> tuple[ToolCall, ...] | None:
        kind = event.get("type")
        rid = event.get("response_id") or event.get("response", {}).get("id")
        if rid != self.response_id or self.finished:
            return None
        if kind == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                call_id, arguments = item.get("call_id"), item.get("arguments")
                if (not call_id or not isinstance(arguments, str) or len(arguments) > 16000
                        or len(self.items) >= 8):
                    self.invalid = True
                else:
                    call = ToolCall(call_id, item.get("name", ""), arguments)
                    if call_id in self.items and self.items[call_id] != call:
                        self.invalid = True
                    self.items[call_id] = call
        elif kind == "response.done":
            self.finished = True
            if self.invalid or event.get("response", {}).get("status") != "completed":
                return ()
            # All complete calls are returned together for atomic conflict checking.
            return tuple(self.items.values())
        return None
