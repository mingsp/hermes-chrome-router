from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from router.gateway_acceptance import gateway_acceptance_report


def _ready_env(**overrides: str) -> dict[str, str]:
    env = {
        "HERMES_CHROME_ROUTER_TOKEN": "prod-router-token",
        "HERMES_CHROME_CLOUD_BRIDGE_URL": "https://web-ui.internal/chrome-bridge",
        "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
        "HERMES_CHROME_ROUTER_PUBLIC_URL": "wss://router.example.com/bridge",
        "HERMES_CHROME_REDIS_URL": "redis://redis.internal:6379/0",
        "HERMES_CHROME_ROUTER_SERVER_ID": "router-b",
        "HERMES_CHROME_ROUTE_TTL_SECONDS": "60",
        "HERMES_CHROME_ROUTER_PEERS": '{"router-a":"http://router-a.internal:8787"}',
        "HERMES_CHROME_ROUTER_AUDIT_DB": "/var/lib/hermes/chrome-router-audit.sqlite3",
        "HERMES_CHROME_ROUTER_STATUS_URL": "https://router-b.internal",
        "HERMES_CHROME_FAILOVER_PROFILE_ID": "xuxiaofeng_profile",
        "HERMES_CHROME_FAILOVER_EXPECTED_SERVER_ID": "router-b",
        "HERMES_CHROME_FAILOVER_EXPECTED_DEVICE_ID": "span-macbook",
        "HERMES_CHROME_FAILOVER_FROM_MS": "1718500000000",
    }
    env.update(overrides)
    return env


@pytest.mark.asyncio
async def test_gateway_acceptance_reports_ready_after_failover_drill():
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fetch_json(url: str, headers: dict[str, str] | None = None):
        calls.append((url, headers))
        parsed = urlparse(url)
        if parsed.path == "/status":
            return {"serverId": "router-b"}
        if parsed.path == "/status/profile/xuxiaofeng_profile":
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": True,
                "extensionConnected": True,
            }
        if parsed.path == "/audit/commands":
            query = parse_qs(parsed.query)
            assert query["profileId"] == ["xuxiaofeng_profile"]
            assert query["action"] == ["tab.list"]
            assert query["status"] == ["completed"]
            assert query["fromMs"] == ["1718500000000"]
            return {"total": 1, "items": [{"commandId": "cmd_after_failover"}]}
        raise AssertionError(f"unexpected URL {url}")

    report = await gateway_acceptance_report(_ready_env(), fetch_json=fetch_json)

    assert report["status"] == "ready"
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["failover-router-server"]["status"] == "pass"
    assert checks["failover-profile-online"]["status"] == "pass"
    assert checks["failover-audit-action-tab-list"]["status"] == "pass"
    assert all(headers == {"authorization": "Bearer prod-router-token"} for _, headers in calls)


@pytest.mark.asyncio
async def test_gateway_acceptance_blocks_when_failover_evidence_is_missing():
    async def fetch_json(url: str, _headers: dict[str, str] | None = None):
        parsed = urlparse(url)
        if parsed.path == "/status":
            return {"serverId": "router-a"}
        if parsed.path == "/status/profile/xuxiaofeng_profile":
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": False,
                "extensionConnected": False,
            }
        if parsed.path == "/audit/commands":
            return {"total": 0, "items": []}
        raise AssertionError(f"unexpected URL {url}")

    report = await gateway_acceptance_report(_ready_env(), fetch_json=fetch_json)

    assert report["status"] == "blocked"
    failed = {check["id"]: check for check in report["checks"] if check["status"] == "fail"}
    assert "failover-router-server" in failed
    assert "failover-profile-online" in failed
    assert "failover-audit-action-tab-list" in failed
