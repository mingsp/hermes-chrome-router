from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


ROUTES_HASH_KEY = "hermes-chrome:profile-routes"
PROFILE_ROUTE_ALIVE_PREFIX = "hermes-chrome:profile-route:"
UNREGISTER_ROUTE_SCRIPT = """
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if not raw then
  return 0
end
local ok, route = pcall(cjson.decode, raw)
if not ok then
  return 0
end
if route["serverId"] == ARGV[2] and route["deviceId"] == ARGV[3] then
  redis.call("HDEL", KEYS[1], ARGV[1])
  redis.call("DEL", KEYS[2])
  return 1
end
return 0
"""


@dataclass(frozen=True)
class ProfileRoute:
    profile_id: str
    server_id: str
    device_id: str


class RedisProfileRouteTable:
    def __init__(self, redis_client: Any):
        self._redis = redis_client

    async def register_profiles(
        self,
        *,
        server_id: str,
        device_id: str,
        profile_ids: tuple[str, ...],
        ttl_seconds: int,
    ) -> None:
        ttl = max(1, int(ttl_seconds))
        for profile_id in profile_ids:
            payload = {
                "serverId": server_id,
                "deviceId": device_id,
                "profileId": profile_id,
            }
            await self._redis.hset(ROUTES_HASH_KEY, profile_id, json.dumps(payload, sort_keys=True))
            await self._redis.setex(_alive_key(profile_id), ttl, "alive")

    async def unregister_profiles(
        self,
        *,
        server_id: str,
        device_id: str,
        profile_ids: tuple[str, ...],
    ) -> None:
        for profile_id in profile_ids:
            await self._redis.eval(
                UNREGISTER_ROUTE_SCRIPT,
                2,
                ROUTES_HASH_KEY,
                _alive_key(profile_id),
                profile_id,
                server_id,
                device_id,
            )

    async def get_profile_route(self, profile_id: str) -> ProfileRoute | None:
        raw = await self._redis.hget(ROUTES_HASH_KEY, profile_id)
        if raw is None:
            return None
        if not await self._route_is_alive(profile_id):
            await self._redis.hdel(ROUTES_HASH_KEY, profile_id)
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            return None
        server_id = str(payload.get("serverId") or "").strip()
        device_id = str(payload.get("deviceId") or "").strip()
        route_profile_id = str(payload.get("profileId") or profile_id).strip()
        if not server_id or not device_id or not route_profile_id:
            return None
        return ProfileRoute(
            profile_id=route_profile_id,
            server_id=server_id,
            device_id=device_id,
        )

    async def _route_is_alive(self, profile_id: str) -> bool:
        exists = getattr(self._redis, "exists", None)
        if exists is None:
            return True
        return bool(await exists(_alive_key(profile_id)))

    async def close(self) -> None:
        close = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
        if close is None:
            return
        result = close()
        if hasattr(result, "__await__"):
            await result


def create_redis_profile_route_table(redis_url: str) -> RedisProfileRouteTable:
    import redis.asyncio as redis

    return RedisProfileRouteTable(redis.from_url(redis_url, decode_responses=True))


def _alive_key(profile_id: str) -> str:
    return f"{PROFILE_ROUTE_ALIVE_PREFIX}{profile_id}"
