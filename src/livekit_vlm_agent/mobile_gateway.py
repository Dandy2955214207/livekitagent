"""Optional local TLS gateway for a phone; backend services remain on loopback."""

import asyncio
import html
import json
from pathlib import Path
import ssl

import aiohttp
from aiohttp import web

from .config import PROJECT_ROOT, Settings

MOBILE = PROJECT_ROOT / ".runtime/mobile"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


def phone_access(profile):
    @web.middleware
    async def allowed(request, handler):
        allowed_ips = {"127.0.0.1", profile.get("computer_ip"), profile.get("phone_ip")}
        if profile.get("phone_ip") and request.remote not in allowed_ips:
            raise web.HTTPForbidden(text="This test gateway is restricted to the configured phone.")
        return await handler(request)
    return allowed


def proxy_app(profile, settings, session, *, livekit_port=7880):
    app = web.Application(client_max_size=64 * 1024, middlewares=[phone_access(profile)])

    async def proxy(request):
        if (request.path.startswith(("/api/internal/", "/api/test-reports"))
                or request.path == "/mobile-test-report.html"):
            raise web.HTTPForbidden()
        if settings.mobile_probe_only:
            if request.path == "/":
                raise web.HTTPFound("/device-check.html")
            if request.path != "/device-check.html" and not request.path.startswith("/assets/"):
                raise web.HTTPForbidden(text="Device check only; cloud sessions are not enabled on this endpoint.")
        livekit = request.path == "/livekit" or request.path.startswith("/livekit/")
        raw_path = request.raw_path[len("/livekit"):] if livekit else request.raw_path
        target = f"http://127.0.0.1:{livekit_port if livekit else settings.api_port}" + (raw_path or "/")
        if livekit and request.headers.get("Upgrade", "").lower() == "websocket":
            protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
            async with session.ws_connect(target, protocols=protocols, max_msg_size=8 * 1024 * 1024) as upstream:
                downstream = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (), max_msg_size=8 * 1024 * 1024)
                await downstream.prepare(request)

                async def relay(source, destination):
                    async for message in source:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            await destination.send_str(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await destination.send_bytes(message.data)
                        else:
                            break

                tasks = [asyncio.create_task(relay(upstream, downstream)), asyncio.create_task(relay(downstream, upstream))]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await downstream.close()
                return downstream
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() not in HOP_HEADERS | {"x-livekit-mobile-gateway"}}
        headers["X-LiveKit-Mobile-Gateway"] = "1"
        async with session.request(request.method, target, data=await request.read(), headers=headers, allow_redirects=False) as upstream:
            response_headers = {key: value for key, value in upstream.headers.items() if key.lower() not in HOP_HEADERS}
            return web.Response(status=upstream.status, body=await upstream.read(), headers=response_headers)

    app.router.add_route("*", "/{path:.*}", proxy)
    return app


def bootstrap_app(profile):
    app = web.Application(middlewares=[phone_access(profile)])

    async def index(request):
        origin = html.escape(profile["origin"])
        text = f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>手机测试接入</title><body style='font-family:system-ui;max-width:700px;margin:30px auto;padding:20px;line-height:1.8'>
        <h1>手机测试接入</h1><p>此HTTP页只提供公开测试证书，不请求摄像头或麦克风。</p>
        <p><a href='/test-ca.cer'>下载本项目测试CA证书</a></p><p>SHA256：</p><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>{profile['ca_sha256']}</pre>
        <p>仅在确认是本项目证书后，由你手动完成信任配置。不要忽略HTTPS证书错误继续测试。</p>
        <p><a href='{origin}/device-check.html'>打开HTTPS能力检测页</a></p><p>证书私钥只保存在电脑项目目录，不提供下载。</p></body></html>"""
        return web.Response(text=text, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def certificate(request):
        return web.FileResponse(MOBILE / "livekit-mobile-test-ca.cer", headers={"Content-Type": "application/pkix-cert"})

    app.router.add_get("/", index)
    app.router.add_get("/test-ca.cer", certificate)
    return app


async def main(stop_event=None):
    profile = json.loads((MOBILE / "profile.json").read_text(encoding="utf-8-sig"))
    if not profile.get("phone_ip"):
        raise RuntimeError("A specific phone IP allowlist is required before binding the mobile gateway")
    settings = Settings()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(MOBILE / "server.pem", MOBILE / "server-key.pem")
    async with aiohttp.ClientSession(auto_decompress=False) as session:
        secure = web.AppRunner(proxy_app(profile, settings, session), access_log=None)
        bootstrap = web.AppRunner(bootstrap_app(profile), access_log=None)
        await secure.setup(); await bootstrap.setup()
        try:
            await web.TCPSite(secure, profile["computer_ip"], profile["https_port"], ssl_context=tls).start()
            await web.TCPSite(bootstrap, profile["computer_ip"], profile["bootstrap_port"]).start()
            print("Mobile TLS gateway ready", flush=True)
            await (stop_event or asyncio.Event()).wait()
        finally:
            await bootstrap.cleanup(); await secure.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
