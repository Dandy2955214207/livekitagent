"""Exercise a local fixture Compose gateway with a disposable invite."""
import asyncio
import os

from aiohttp import ClientSession
from harmony_gateway_rtc import ready, until, verify_call


async def main():
    url = "http://127.0.0.1:" + str(int(os.getenv("GATEWAY_PORT", "18095")))
    headers = {"Authorization": "Bearer " + os.environ["HARMONY_TRIAL_CODE"]}
    async with ClientSession() as client:
        for path, expected in (("/ready", 200), ("/api/sessions", 404), ("/trial/access", 401),
                               ("/api/test-reports", 404), ("/v1/calls", 401)):
            async with client.get(url + path) as response:
                assert response.status == expected, (path, response.status)
        async with client.ws_connect(url + "/v1/calls", headers=headers) as socket:
            await verify_call(socket)
            async with client.ws_connect(url + "/v1/calls", headers=headers) as other:
                await other.send_json({"type": "start", "version": 1})
                error = await until(other, lambda data: isinstance(data, dict) and data.get("type") == "error")
                assert error["code"] == "busy"
            await socket.send_json({"type": "end"})
            await until(socket, lambda data: isinstance(data, dict) and data.get("state") == "ended")
        # Rapid redials exercise LiveKit's transient worker slot reservation.
        for _ in range(12):
            async with client.ws_connect(url + "/v1/calls", headers=headers) as socket:
                await ready(socket)
                await socket.send_json({"type": "end"})
                await until(socket, lambda data: isinstance(data, dict) and data.get("state") == "ended")
        print("OK native invite, private routes, busy, hangup and 12 consecutive redials")


if __name__ == "__main__":
    asyncio.run(main())
