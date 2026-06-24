from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass
class WebUIBindingProvider:
    base_url: str
    token: str | None = None
    session: aiohttp.ClientSession | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.token = (self.token or "").strip()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def _load_binding_items(self) -> list[dict[str, Any]]:
        owns_session = self.session is None
        session = self.session or aiohttp.ClientSession()
        try:
            async with session.get(
                f"{self.base_url}/api/hermes/browser/profile-bindings",
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json()
        finally:
            if owns_session:
                await session.close()

        bindings = payload.get("bindings") if isinstance(payload, dict) else None
        if not isinstance(bindings, list):
            return []
        return [item for item in bindings if isinstance(item, dict)]

    async def load_binding_snapshot(self) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        values: dict[str, str] = {}
        policies: dict[str, dict[str, Any]] = {}
        for item in await self._load_binding_items():
            if item.get("enabled") is False:
                continue
            profile_id = str(item.get("profile_id") or item.get("profileId") or "").strip()
            device_id = str(item.get("device_id") or item.get("deviceId") or "").strip()
            if not (profile_id and device_id):
                continue
            values[profile_id] = device_id
            action_policy = item.get("action_policy") or item.get("actionPolicy") or {}
            if isinstance(action_policy, dict) and action_policy:
                policies[profile_id] = dict(action_policy)
        return values, policies

    async def load_bindings(self) -> dict[str, str]:
        values, _policies = await self.load_binding_snapshot()
        return values

    async def load_binding_policies(self) -> dict[str, dict[str, Any]]:
        _values, policies = await self.load_binding_snapshot()
        return policies
