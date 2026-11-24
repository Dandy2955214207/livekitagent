"""Real provider test worker; log only aggregate levels of synthetic audio."""
import logging

from livekit_vlm_agent.official.bridge import OfficialBridge

original_push_audio = OfficialBridge.push_audio


async def measured_push_audio(self, frame):
    self.probe_frames = getattr(self, 'probe_frames', 0) + 1
    self.probe_peak = max(getattr(self, 'probe_peak', 0), max(map(abs, frame.data), default=0))
    if self.probe_frames == 1 or self.probe_frames % 500 == 0:
        logging.getLogger('memory_probe').info('synthetic_audio frames=%s peak=%s', self.probe_frames, self.probe_peak)
    await original_push_audio(self, frame)


OfficialBridge.push_audio = measured_push_audio

from livekit_vlm_agent.worker import server
from livekit.agents import cli

if __name__ == '__main__':
    cli.run_app(server)
