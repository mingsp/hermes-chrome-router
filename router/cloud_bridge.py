from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from shared.errors import ProtocolError
from shared.protocol import CloudCommand, parse_cloud_next_payload


@dataclass(frozen=True)
class MalformedCloudCommand:
    id: str
    error: str


class CloudBridgeClient:
    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
        token: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._token = (token or "").strip()

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def poll_next(self, router_id: str) -> CloudCommand | MalformedCloudCommand | None:
        owns_session = self._session is None
        session = self._session or aiohttp.ClientSession()
        try:
            async with session.get(
                f"{self.base_url}/next",
                params={"name": f"router:{router_id}"},
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json()
                try:
                    return parse_cloud_next_payload(payload)
                except ProtocolError as exc:
                    command = payload.get("command") if isinstance(payload, dict) else None
                    command_id = command.get("id") if isinstance(command, dict) else None
                    if isinstance(command_id, str) and command_id.strip():
                        return MalformedCloudCommand(id=command_id.strip(), error=str(exc))
                    raise
        finally:
            if owns_session:
                await session.close()

    async def post_result(
        self,
        command_id: str,
        ok: bool,
        result=None,
        error: str | None = None,
    ) -> None:
        owns_session = self._session is None
        session = self._session or aiohttp.ClientSession()
        try:
            payload = {"id": command_id, "ok": ok}
            if ok:
                payload["result"] = result
            else:
                payload["error"] = error or "Chrome command failed"
            async with session.post(
                f"{self.base_url}/result",
                json=payload,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
        finally:
            if owns_session:
                await session.close()
