"""Direct dialogue plus independent experimental text observation."""

from ..media.fanout import VideoFanout
from .qwen_realtime import QwenRealtimeModel, QwenRealtimeSession
from ..observation.background import BackgroundObserver, visual_supported


def direct_capabilities(settings=None):
    return {"stage": "D2_text_observation", "dialogue": "direct",
            "observation": "experimental_text" if settings and visual_supported(settings) else "unavailable", "proactive_audio": "unavailable",
            "task_tools": "unavailable", "memory_injection": "unavailable",
            "local_memory": "available", "task_registration": "ui_text" if settings and visual_supported(settings) else "paused_until_protocol_verified"}


class QwenLiveModel(QwenRealtimeModel):
    def session(self, *, turn_detection_disabled=False):
        session = QwenLiveSession(self, manual=turn_detection_disabled)
        self.sessions.add(session)
        return session


class QwenLiveSession(QwenRealtimeSession):
    def __init__(self, model, *, manual=False):
        super().__init__(model, manual=manual)
        self.background_frames = VideoFanout()
        self.video.on_encoded = self.background_frames.publish
        self.direct_state = None
        self.observer = None

    def attach_direct_state(self, state):
        self.direct_state = state
        if visual_supported(self.settings):
            state.observation_available = True
            self.observer = BackgroundObserver(self, state)

    def _on_event(self, event, pcm):
        super()._on_event(event, pcm)
        state = getattr(self, 'direct_state', None)
        if state and not self._closed:
            if event.get('type') == 'conversation.item.input_audio_transcription.completed':
                state.record_turn(source_id=event.get('item_id', ''), text=event.get('transcript', ''), role='user')
            elif event.get('type') == 'response.done':
                response = self._client.responses.get(event['response']['id'])
                if response and response.status == 'completed':
                    state.record_turn(source_id=response.id, text=response.text, role='assistant')

    def clear_video(self):
        super().clear_video()
        self.background_frames.clear()
        if self.direct_state:
            self.direct_state.tasks.camera_changed(self.video.epoch)

    async def aclose(self):
        if self.observer:
            await self.observer.aclose()
        await super().aclose()
        self.background_frames.clear()
        if self.direct_state:
            await self.direct_state.aclose()
