import pytest

from router.registry import RouterRegistry, TransitCommand
from shared.errors import DeliveryError


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def test_registry_registers_device_profiles():
    registry = RouterRegistry(bindings={"xuxiaofeng_profile": "span-macbook"})
    ws = FakeWS()

    registry.register("span-macbook", ("xuxiaofeng_profile",), ws)

    assert registry.connection_for_profile("xuxiaofeng_profile").device_id == "span-macbook"


def test_registry_records_heartbeat_extension_state():
    registry = RouterRegistry(bindings={"xuxiaofeng_profile": "span-macbook"})
    registry.register("span-macbook", ("xuxiaofeng_profile",), FakeWS())

    registry.record_heartbeat(
        "span-macbook",
        extension_connected=True,
        extension_version="0.15.38",
    )

    details = registry.status()["connectionDetails"]["span-macbook"]
    assert details["deviceId"] == "span-macbook"
    assert details["profileIds"] == ["xuxiaofeng_profile"]
    assert details["extensionConnected"] is True
    assert details["extensionVersion"] == "0.15.38"
    assert details["lastHeartbeatAt"] is not None


def test_registry_rejects_offline_profile():
    registry = RouterRegistry(bindings={"xuxiaofeng_profile": "span-macbook"})

    with pytest.raises(DeliveryError, match="not connected"):
        registry.connection_for_profile("xuxiaofeng_profile")


def test_registry_tracks_inflight_and_profile_mismatch():
    registry = RouterRegistry(bindings={"xuxiaofeng_profile": "span-macbook"})
    command = TransitCommand(
        id="cmd_1",
        profile_id="xuxiaofeng_profile",
        cloud_bridge_url="http://cloud",
        action="page.click",
        params={},
        timeout_ms=28000,
    )

    registry.add_inflight(command)

    assert registry.pop_inflight("cmd_1", "xuxiaofeng_profile") == command
    registry.add_inflight(command)
    with pytest.raises(DeliveryError, match="profileId mismatch"):
        registry.pop_inflight("cmd_1", "other_profile")


def test_unregister_returns_inflight_commands_for_device():
    registry = RouterRegistry(bindings={"xuxiaofeng_profile": "span-macbook"})
    ws = FakeWS()
    registry.register("span-macbook", ("xuxiaofeng_profile",), ws)
    command = TransitCommand(
        id="cmd_1",
        profile_id="xuxiaofeng_profile",
        cloud_bridge_url="http://cloud",
        action="page.click",
        params={},
        timeout_ms=28000,
    )
    registry.add_inflight(command)

    failed = registry.unregister("span-macbook")

    assert failed == [command]


def test_registry_records_profile_command_stats_and_error_rate():
    registry = RouterRegistry(bindings={"xuxiaofeng_profile": "span-macbook"})

    registry.record_command_result("xuxiaofeng_profile", ok=True)
    registry.record_command_result("xuxiaofeng_profile", ok=False)

    stats = registry.status()["commandStats"]["xuxiaofeng_profile"]
    assert stats == {
        "total": 2,
        "success": 1,
        "failure": 1,
        "errorRate": 0.5,
    }
    assert registry.profile_status("xuxiaofeng_profile")["commandStats"] == stats
