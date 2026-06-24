import json

from router.route_table import RedisProfileRouteTable


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.ttl_values = {}
        self.deleted = []
        self.after_hget = None
        self.before_eval = None

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key, field):
        value = self.hashes.get(key, {}).get(field)
        if self.after_hget is not None:
            await self.after_hget()
        return value

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    async def setex(self, key, ttl, value):
        self.ttl_values[key] = (ttl, value)

    async def delete(self, key):
        self.deleted.append(key)
        self.ttl_values.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.ttl_values else 0

    async def eval(self, _script, key_count, *keys_and_args):
        assert key_count == 2
        if self.before_eval is not None:
            await self.before_eval()
        routes_key, alive_key, profile_id, server_id, device_id = keys_and_args
        raw = self.hashes.get(routes_key, {}).get(profile_id)
        if raw is None:
            return 0
        payload = json.loads(raw)
        if payload.get("serverId") != server_id or payload.get("deviceId") != device_id:
            return 0
        await self.hdel(routes_key, profile_id)
        await self.delete(alive_key)
        return 1


async def test_redis_route_table_registers_profile_routes_with_ttl():
    redis = FakeRedis()
    table = RedisProfileRouteTable(redis)

    await table.register_profiles(
        server_id="router-a",
        device_id="span-macbook",
        profile_ids=("default", "xuxiaofeng_profile"),
        ttl_seconds=60,
    )

    default_route = json.loads(redis.hashes["hermes-chrome:profile-routes"]["default"])
    assert default_route == {
        "serverId": "router-a",
        "deviceId": "span-macbook",
        "profileId": "default",
    }
    assert redis.ttl_values["hermes-chrome:profile-route:default"] == (60, "alive")
    assert redis.ttl_values["hermes-chrome:profile-route:xuxiaofeng_profile"] == (60, "alive")


async def test_redis_route_table_unregisters_only_matching_server_and_device():
    redis = FakeRedis()
    table = RedisProfileRouteTable(redis)
    await redis.hset(
        "hermes-chrome:profile-routes",
        "default",
        json.dumps({"serverId": "router-b", "deviceId": "span-macbook", "profileId": "default"}),
    )

    await table.unregister_profiles(
        server_id="router-a",
        device_id="span-macbook",
        profile_ids=("default",),
    )

    assert "default" in redis.hashes["hermes-chrome:profile-routes"]
    assert redis.deleted == []

    await redis.hset(
        "hermes-chrome:profile-routes",
        "default",
        json.dumps({"serverId": "router-a", "deviceId": "span-macbook", "profileId": "default"}),
    )
    await table.unregister_profiles(
        server_id="router-a",
        device_id="span-macbook",
        profile_ids=("default",),
    )

    assert "default" not in redis.hashes["hermes-chrome:profile-routes"]
    assert redis.deleted == ["hermes-chrome:profile-route:default"]


async def test_redis_route_table_ignores_stale_hash_route_when_alive_key_expired():
    redis = FakeRedis()
    table = RedisProfileRouteTable(redis)
    await redis.hset(
        "hermes-chrome:profile-routes",
        "default",
        json.dumps({"serverId": "router-a", "deviceId": "span-macbook", "profileId": "default"}),
    )

    route = await table.get_profile_route("default")

    assert route is None
    assert "default" not in redis.hashes["hermes-chrome:profile-routes"]


async def test_redis_route_table_unregister_does_not_delete_route_replaced_after_read():
    redis = FakeRedis()
    table = RedisProfileRouteTable(redis)
    await redis.hset(
        "hermes-chrome:profile-routes",
        "default",
        json.dumps({"serverId": "router-a", "deviceId": "span-macbook", "profileId": "default"}),
    )
    await redis.setex("hermes-chrome:profile-route:default", 60, "alive")

    async def replace_route():
        redis.after_hget = None
        await redis.hset(
            "hermes-chrome:profile-routes",
            "default",
            json.dumps({"serverId": "router-b", "deviceId": "lab-mac", "profileId": "default"}),
        )
        await redis.setex("hermes-chrome:profile-route:default", 60, "alive")

    redis.after_hget = replace_route
    redis.before_eval = replace_route

    await table.unregister_profiles(
        server_id="router-a",
        device_id="span-macbook",
        profile_ids=("default",),
    )

    assert json.loads(redis.hashes["hermes-chrome:profile-routes"]["default"]) == {
        "serverId": "router-b",
        "deviceId": "lab-mac",
        "profileId": "default",
    }
    assert redis.ttl_values["hermes-chrome:profile-route:default"] == (60, "alive")
