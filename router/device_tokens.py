from __future__ import annotations

from dataclasses import dataclass

import aiohttp


@dataclass
class WebUIDeviceTokenVerifier:
    base_url: str
    session: aiohttp.ClientSession | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    async def verify_device_token(self, token: str) -> str | None:
        token = (token or "").strip()
        if not token:
            return None

        owns_session = self.session is None
        session = self.session or aiohttp.ClientSession()
        try:
            async with session.post(
                f"{self.base_url}/api/devices/bridge-token/verify",
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
        finally:
            if owns_session:
                await session.close()

        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return None
        device_id = str(payload.get("device_id") or payload.get("deviceId") or "").strip()
        return device_id or None
