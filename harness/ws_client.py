from __future__ import annotations

import asyncio
import contextlib
import time

import aiohttp

from shared.timing import HEARTBEAT_INTERVAL_SECONDS


class RouterWebSocketClient:
    def __init__(
        self,
        router_url: str,
        token: str,
        device_id: str,
        profile_ids: tuple[str, ...],
        local_bridge,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        reconnect_delay: float = 1.0,
    ):
        self.router_url = router_url
        self.token = token
        self.device_id = device_id
        self.profile_ids = profile_ids
        self.local_bridge = local_bridge
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delay = reconnect_delay
        self._ws = None
        self._closed = False

    async def send_result(self, payload: dict) -> None:
        if self._ws is not None:
            await self._ws.send_json(payload)

    async def close(self) -> None:
        self._closed = True
        if self.local_bridge is not None:
            profile_id = self.profile_ids[0] if self.profile_ids else "unknown"
            await self.local_bridge.fail_all_pending(
                f"browser bridge disconnected for profile {profile_id}"
            )
            await asyncio.sleep(0.01)
        if self._ws is not None:
            await self._ws.close()

    async def run_once(self) -> None:
        if self._closed:
            return
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.router_url) as ws:
                self._ws = ws
                await ws.send_json(
                    {
                        "type": "hello",
                        "deviceId": self.device_id,
                        "token": self.token,
                        "profileIds": list(self.profile_ids),
                        "clientVersion": "0.1.0",
                    }
                )
                heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                try:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        payload = msg.json()
                        if payload.get("type") == "command":
                            await self.local_bridge.enqueue_command(payload)
                        elif payload.get("type") == "heartbeat_ack":
                            continue
                        elif payload.get("type") == "error":
                            break
                finally:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
                    self._ws = None
                    if self.local_bridge is not None:
                        await self.local_bridge.fail_all_pending("Router connection closed")

    async def run_forever(self) -> None:
        while not self._closed:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if not self._closed:
                await asyncio.sleep(self.reconnect_delay)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            if self._ws is None:
                return
            await self._ws.send_json(
                {
                    "type": "heartbeat",
                    "timestamp": int(time.time() * 1000),
                    "extensionConnected": bool(
                        getattr(self.local_bridge, "extension_connected", False)
                    ),
                    "extensionVersion": getattr(self.local_bridge, "_extension_version", None),
                }
            )
