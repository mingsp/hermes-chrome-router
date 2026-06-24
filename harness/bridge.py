from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from aiohttp import web

from shared.timing import LOCAL_COMMAND_TIMEOUT_MS, LOCAL_NEXT_LONG_POLL_SECONDS


def _browser_origin_allowed(headers) -> bool:
    origin = headers.get("origin", "")
    if origin:
        return origin.startswith("chrome-extension://")
    sec_fetch_site = headers.get("sec-fetch-site", "")
    return sec_fetch_site in ("", "none", "same-origin")


class LocalBridge:
    def __init__(
        self,
        result_sink,
        extension_version: str = "0.15.38",
        long_poll_seconds: float = LOCAL_NEXT_LONG_POLL_SECONDS,
    ):
        self._result_sink = result_sink
        self._extension_version = extension_version
        self._long_poll_seconds = long_poll_seconds
        self._queue: list[dict[str, Any]] = []
        self._delivered: dict[str, dict[str, Any]] = {}
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        self._cond = asyncio.Condition()
        self.last_seen_at: float | None = None
        self.client_name: str | None = None

    @property
    def extension_connected(self) -> bool:
        return self.last_seen_at is not None and time.time() - self.last_seen_at < 300

    async def enqueue_command(self, frame: dict[str, Any]) -> None:
        async with self._cond:
            self._queue.append(frame)
            self._timeout_tasks[frame["id"]] = asyncio.create_task(self._timeout_command(frame, delivered=False))
            self._cond.notify_all()

    async def fail_all_pending(self, error: str) -> None:
        frames = list(self._queue) + list(self._delivered.values())
        self._queue.clear()
        self._delivered.clear()
        for task in list(self._timeout_tasks.values()):
            task.cancel()
        self._timeout_tasks.clear()
        for frame in frames:
            await self._result_sink.send_result(
                {
                    "type": "result",
                    "id": frame["id"],
                    "profileId": frame["profileId"],
                    "ok": False,
                    "error": error,
                }
            )

    async def _timeout_command(self, frame: dict[str, Any], *, delivered: bool) -> None:
        requested_timeout_ms = int(frame.get("timeoutMs") or LOCAL_COMMAND_TIMEOUT_MS)
        timeout_ms = min(requested_timeout_ms, LOCAL_COMMAND_TIMEOUT_MS)
        timeout_seconds = timeout_ms / 1000
        if not delivered:
            timeout_seconds += min(self._long_poll_seconds, 0.01)
        await asyncio.sleep(timeout_seconds)
        command_id = frame["id"]
        active_frame = self._delivered.pop(command_id, None) if delivered else self._remove_queued(command_id)
        if active_frame is None:
            return
        state = "did not return a result" if delivered else "not polling"
        self._timeout_tasks.pop(command_id, None)
        await self._result_sink.send_result(
            {
                "type": "result",
                "id": command_id,
                "profileId": frame["profileId"],
                "ok": False,
                "error": f"Chrome extension {state} for command {command_id}",
            }
        )

    def _remove_queued(self, command_id: str) -> dict[str, Any] | None:
        for index, item in enumerate(self._queue):
            if item.get("id") == command_id:
                return self._queue.pop(index)
        return None

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/status", self._status)
        app.router.add_get("/next", self._next)
        app.router.add_post("/result", self._result)
        app.router.add_post("/command", self._command)
        app.on_cleanup.append(self._cleanup)
        return app

    async def _cleanup(self, _app: web.Application) -> None:
        for task in list(self._timeout_tasks.values()):
            task.cancel()
        if self._timeout_tasks:
            await asyncio.gather(*self._timeout_tasks.values(), return_exceptions=True)
        self._timeout_tasks.clear()
        self._queue.clear()
        self._delivered.clear()

    async def _status(self, request: web.Request) -> web.Response:
        local_url = f"{request.scheme}://{request.host}"
        return web.json_response(
            {
                "url": local_url,
                "connected": self.extension_connected,
                "lastSeenAt": self.last_seen_at,
                "clientName": self.client_name,
                "queuedCommands": len(self._queue),
                "pendingCommands": len(self._delivered),
            }
        )

    async def _next(self, request: web.Request) -> web.Response:
        if not _browser_origin_allowed(request.headers):
            return web.json_response({"ok": False, "error": "browser origin not allowed"}, status=403)
        self.last_seen_at = time.time()
        self.client_name = request.query.get("name")
        deadline = time.monotonic() + self._long_poll_seconds
        async with self._cond:
            while not self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._next_response(
                        {"type": "none", "expectedExtensionVersion": self._extension_version},
                        request,
                    )
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return self._next_response(
                        {"type": "none", "expectedExtensionVersion": self._extension_version},
                        request,
                    )
            frame = self._queue.pop(0)
        command_id = frame["id"]
        timeout_task = self._timeout_tasks.pop(command_id, None)
        if timeout_task is not None:
            timeout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timeout_task
        self._delivered[command_id] = frame
        self._timeout_tasks[command_id] = asyncio.create_task(self._timeout_command(frame, delivered=True))
        return self._next_response(
            {
                "type": "command",
                "command": {
                    "id": frame["id"],
                    "profileId": frame["profileId"],
                    "action": frame["action"],
                    "params": frame.get("params") or {},
                },
                "expectedExtensionVersion": self._extension_version,
            },
            request,
        )

    def _next_response(self, payload: dict[str, Any], request: web.Request) -> web.Response:
        origin = request.headers.get("origin")
        headers = {"x-hermes-chrome-version": self._extension_version}
        if origin and origin.startswith("chrome-extension://"):
            headers["access-control-allow-origin"] = origin
            headers["access-control-expose-headers"] = "x-hermes-chrome-version"
            headers["vary"] = "origin"
        return web.json_response(payload, headers=headers)

    async def _result(self, request: web.Request) -> web.Response:
        if not _browser_origin_allowed(request.headers):
            return web.json_response({"ok": False, "error": "browser origin not allowed"}, status=403)
        payload = await request.json()
        command_id = payload.get("id")
        frame = self._delivered.pop(command_id, None)
        if frame is None:
            return web.json_response({"ok": False, "error": "unknown command id"}, status=404)
        timeout_task = self._timeout_tasks.pop(command_id, None)
        if timeout_task is not None:
            timeout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timeout_task
        result_frame = {
            "type": "result",
            "id": command_id,
            "profileId": frame["profileId"],
            "ok": bool(payload.get("ok")),
        }
        if payload.get("ok"):
            result_frame["result"] = payload.get("result")
        else:
            result_frame["error"] = payload.get("error") or "Chrome extension command failed"
        await self._result_sink.send_result(result_frame)
        return web.json_response({"ok": True})

    async def _command(self, request: web.Request) -> web.Response:
        if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Chrome commands are accepted only from local processes",
                },
                status=403,
            )
        payload = await request.json()
        await self.enqueue_command(payload)
        return web.json_response({"ok": True})
