"""LiveKit AgentServer dispatch for the official Harness."""

import json
import sys

from livekit.agents import AgentServer, JobContext, cli

from .config import Settings
from .official.bridge import check_startup
from .official.worker import run_official_call

settings = Settings()
server = AgentServer(ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key.get_secret_value(), api_secret=settings.livekit_api_secret.get_secret_value(),
    host=settings.worker_host, port=settings.worker_port, num_idle_processes=0,
    load_threshold=settings.worker_load_threshold, log_level="INFO", shutdown_process_timeout=10, session_end_timeout=15)


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext):
    await run_official_call(ctx, settings, json.loads(ctx.job.metadata))


if __name__ == "__main__":
    if sys.argv[1:2] in (["start"], ["dev"]):
        try:
            check_startup(settings)
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from None
    cli.run_app(server)
