"""Loopback session API. Provider credentials never enter the browser response."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import aiohttp
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from pydantic import BaseModel, Field, SecretStr

from .config import PROJECT_ROOT, Settings
from .mobile_test import FRAME_KEY, MAX_BROWSER_BYTES, MobileTests, append_events, timestamp
from .session import SessionBusy, SessionRegistry

logger = logging.getLogger(__name__)


class EndRequest(BaseModel):
    access_token: SecretStr


class WorkerUpdate(BaseModel):
    state: Literal["ready", "error", "closed"]
    error: str | None = None


class BrowserEvent(BaseModel):
    seq: int = Field(gt=0, le=10000)
    at: int = Field(gt=0, le=9999999999999)
    alignedAt: int | None = Field(default=None, gt=0, le=9999999999999)
    kind: Literal["join", "microphone.ready", "microphone.change", "room.connected", "room.reconnecting",
                  "room.reconnected", "room.disconnected", "camera.request", "camera.state", "control",
                  "audio.received", "audio.clear", "audio.end", "playback", "error", "mark", "end"]
    detail: str = Field(default="", max_length=500)
    outputId: int | None = Field(default=None, gt=0)
    taskId: str | None = Field(default=None, max_length=120)


def create_app(settings: Settings | None = None, *, mobile_tests: MobileTests | None = None):
    settings = settings or Settings()
    mobile_tests = mobile_tests or MobileTests()
    capture_lock = asyncio.Lock()
    web = urlsplit(settings.web_origin)
    origins = {settings.web_origin, f"http://127.0.0.1:{web.port or 5173}",
               f"http://127.0.0.1:{settings.api_port}", f"http://localhost:{settings.api_port}"}

    @asynccontextmanager
    async def lifespan(app):
        async with api.LiveKitAPI(url=settings.livekit_url,
            api_key=settings.livekit_api_key.get_secret_value(),
            api_secret=settings.livekit_api_secret.get_secret_value(), timeout=aiohttp.ClientTimeout(total=8)) as client:
            registry = SessionRegistry(settings, client)
            app.state.registry = registry

            async def sweep():
                while True:
                    await asyncio.sleep(2)
                    try:
                        await registry.sweep()
                    except Exception as exc:
                        logger.warning("Session cleanup retry: %s", type(exc).__name__)

            sweeper = asyncio.create_task(sweep())
            try:
                yield
            finally:
                sweeper.cancel()
                await asyncio.gather(sweeper, return_exceptions=True)
                await registry.close()

    app = FastAPI(title="本机语音会话", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.headers.get("origin") and request.headers["origin"] not in origins:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        if request.url.path == "/mobile-test-report.html" or request.url.path.startswith("/api/test-reports"):
            if (request.headers.get("x-livekit-mobile-gateway") == "1" or not request.client
                    or request.client.host not in {"127.0.0.1", "::1"}):
                return JSONResponse({"detail": "Reports are available on this computer."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authorized(request, session_id, authorization, worker=False):
        try:
            bearer = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            return request.app.state.registry.authorize(session_id, bearer, worker=worker)
        except KeyError:
            raise HTTPException(404, "会话不存在或访问凭据无效。") from None

    @app.get("/api/health")
    async def health(request: Request):
        return {"status": "ok", "active_sessions": sum(
            not call.cleaned for call in request.app.state.registry.calls.values())}

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "请使用 JSON 请求。")
        capture_id = None
        try:
            data = await request.json() if await request.body() else {}
        except ValueError:
            raise HTTPException(400, "无效的 JSON 请求。") from None
        if isinstance(data, dict) and "test_capture" in data:
            capture = data["test_capture"]
            if not isinstance(capture, dict) or not isinstance(capture.get("id"), str) or not isinstance(capture.get("token"), str):
                raise HTTPException(400, "缺少测试记录凭据。")
            path = await capture_directory(capture["id"], capture["token"])
            try:
                await asyncio.to_thread(mobile_tests.bind, path)
            except FileExistsError:
                raise HTTPException(409, "该测试记录已绑定一次通话。") from None
            capture_id = capture["id"]
        try:
            return await request.app.state.registry.create(capture_id=capture_id)
        except SessionBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception as exc:
            logger.warning("Session creation failed: %s", type(exc).__name__)
            raise HTTPException(503, "无法创建通话，请确认 LiveKit 和 Worker 已启动。") from None

    async def capture_directory(run_id, token):
        if not token or len(token) > 128:
            raise HTTPException(404, "测试记录不存在或凭据无效。")
        try:
            return await asyncio.to_thread(mobile_tests.authorize, run_id, token)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "测试记录不存在或凭据无效。") from None

    @app.post("/api/mobile-tests", status_code=201)
    async def start_mobile_test():
        return await asyncio.to_thread(mobile_tests.start)

    @app.post("/api/mobile-tests/{run_id}/events")
    async def record_browser_events(run_id: str, request: Request, authorization: str = Header(default="")):
        path = await capture_directory(run_id, authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else "")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 48 * 1024:
                raise HTTPException(413, "事件批次过大。")
        try:
            batch = json.loads(body)
            if not isinstance(batch, list) or not 1 <= len(batch) <= 40:
                raise ValueError("batch_size")
            received = timestamp()
            rows = [{"source": "phone", "receivedAt": received, **BrowserEvent.model_validate(row).model_dump(exclude_none=True)}
                    for row in batch]
        except (ValueError, TypeError):
            raise HTTPException(400, "无效的手机事件。") from None
        try:
            async with capture_lock:
                await asyncio.to_thread(append_events, path / "phone.jsonl", rows, MAX_BROWSER_BYTES)
        except ValueError:
            raise HTTPException(413, "本次手机事件达到记录上限。") from None
        return {"stored": len(rows)}

    def report_directory(run_id):
        try:
            return mobile_tests.directory(run_id)
        except KeyError:
            raise HTTPException(404, "测试记录不存在。") from None

    @app.get("/api/test-reports")
    async def test_reports():
        return await asyncio.to_thread(mobile_tests.list)

    @app.get("/api/test-reports/{run_id}")
    async def report_status(run_id: str):
        path = report_directory(run_id)
        return {"bound": (path / "bound").exists(), "writeError": (path / "write-error").exists()}

    @app.get("/api/test-reports/{run_id}/events/{source}")
    async def report_events(run_id: str, source: Literal["worker", "phone"]):
        path = report_directory(run_id) / (source + ".jsonl")
        if not path.is_file():
            return PlainTextResponse("")
        return FileResponse(path, media_type="application/x-ndjson; charset=utf-8")

    @app.get("/api/test-reports/{run_id}/frames/{key}")
    async def report_frame(run_id: str, key: str):
        if not FRAME_KEY.fullmatch(key):
            raise HTTPException(404, "画面不存在。")
        path = report_directory(run_id) / "frames" / (key + ".jpg")
        if not path.is_file():
            raise HTTPException(404, "画面未留存。")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request, authorization: str = Header(default="")):
        return authorized(request, session_id, authorization).public()

    @app.delete("/api/sessions/{session_id}")
    async def end_session(session_id: str, request: Request, authorization: str = Header(default="")):
        call = authorized(request, session_id, authorization)
        await request.app.state.registry.end(call)
        return call.public()

    @app.post("/api/sessions/{session_id}/end")
    async def beacon_end(session_id: str, body: EndRequest, request: Request):
        try:
            call = request.app.state.registry.authorize(session_id, body.access_token.get_secret_value())
        except KeyError:
            raise HTTPException(404, "会话不存在。") from None
        await request.app.state.registry.end(call)
        return {"state": call.state}

    @app.post("/api/internal/sessions/{session_id}")
    async def worker_status(session_id: str, body: WorkerUpdate, request: Request,
                            authorization: str = Header(default="")):
        call = authorized(request, session_id, authorization, worker=True)
        if call.cleaned:
            return {"state": "closed"}
        import time
        call.worker_seen = time.monotonic()
        if body.state == "ready":
            call.state = "ready"
        else:
            await request.app.state.registry.end(call, error=body.error if body.state == "error" else None)
        return {"state": call.state}

    dist = PROJECT_ROOT / "web/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
