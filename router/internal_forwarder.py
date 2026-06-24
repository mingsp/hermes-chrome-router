from __future__ import annotations

from typing import Any

import aiohttp

from router.registry import TransitCommand


class InternalCommandForwarder:
    def __init__(
        self,
        peer_urls: dict[str, str],
        *,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ):
        self._peer_urls = {
            str(server_id).strip(): str(base_url).strip().rstrip("/")
            for server_id, base_url in peer_urls.items()
            if str(server_id).strip() and str(base_url).strip()
        }
        self._token = (token or "").strip()
        self._session = session

    async def forward_command(self, server_id: str, command: TransitCommand) -> None:
        base_url = self._peer_urls.get(server_id)
        if not base_url:
            raise RuntimeError(f"no peer Router configured for server {server_id}")
        owns_session = self._session is None
        session = self._session or aiohttp.ClientSession()
        try:
            async with session.post(
                f"{base_url}/internal/commands",
                json=transit_command_to_payload(command),
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=max(0.1, command.timeout_ms / 1000)),
            ) as resp:
                resp.raise_for_status()
        finally:
            if owns_session:
                await session.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}


def transit_command_to_payload(command: TransitCommand) -> dict[str, Any]:
    return {
        "id": command.id,
        "profileId": command.profile_id,
        "cloudBridgeUrl": command.cloud_bridge_url,
        "action": command.action,
        "params": command.params,
        "timeoutMs": command.timeout_ms,
    }
