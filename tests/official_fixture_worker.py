"""Isolated RTC fixture entry point; no product flag can select this provider."""
from pathlib import Path
import os

import livekit_vlm_agent.official.bridge as bridge
bridge.BRIDGE = Path(__file__).resolve().parents[1] / 'harness/qwen-live/test/fixture-bridge.mjs'
if os.environ.get('OFFICIAL_TEST_SCENE') == 'teach':
    bridge.BRIDGE = bridge.BRIDGE.with_name('scene-fixture-bridge.mjs')
elif os.environ.get('OFFICIAL_TEST_SCENE') == 'guide':
    bridge.BRIDGE = bridge.BRIDGE.with_name('guide-fixture-bridge.mjs')
elif os.environ.get('OFFICIAL_TEST_SCENE') == 'report':
    bridge.BRIDGE = bridge.BRIDGE.with_name('report-fixture-bridge.mjs')

from livekit_vlm_agent.worker import server
from livekit.agents import cli

if __name__ == '__main__':
    cli.run_app(server)
