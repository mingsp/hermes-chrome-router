from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from router.acceptance_bundle import (
    acceptance_bundle_report,
    write_acceptance_bundle_report,
)


def _acceptance_env() -> dict[str, str]:
    return {
        "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
        "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
        "HERMES_CHROME_ROUTER_TOKEN": "prod-router-token",
        "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
        "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
        "HERMES_CHROME_E2E_FROM_MS": "1718500000000",
        "HERMES_CHROME_CLOUD_BRIDGE_URL": "https://web-ui.internal/chrome-bridge",
        "HERMES_CHROME_ROUTER_PUBLIC_URL": "wss://router.example.com/bridge",
        "HERMES_CHROME_ROUTER_AUDIT_DB": "/var/lib/hermes/chrome-router-audit.sqlite3",
    }


@pytest.mark.asyncio
async def test_acceptance_bundle_reports_ready_when_e2e_is_ready_for_internal_acceptance(tmp_path):
    async def fetch_json(url: str, headers: dict[str, str] | None = None):
        parsed = urlparse(url)
        if url == "https://hermes.example.com/api/devices/local-client/update":
            return {"version": "0.2.0"}
        if url == "http://127.0.0.1:16319/status":
            return {
                "connected": True,
                "clientName": "hermes-extension",
                "queuedCommands": 0,
                "pendingCommands": 0,
            }
        if parsed.path == "/status/profile/xuxiaofeng_profile":
            assert headers == {"authorization": "Bearer prod-router-token"}
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": True,
                "extensionConnected": True,
                "toolGate": {"ready": True},
            }
        if parsed.path == "/audit/commands":
            query = parse_qs(parsed.query)
            assert query["profileId"] == ["xuxiaofeng_profile"]
            assert query["status"] == ["completed"]
            assert query["fromMs"] == ["1718500000000"]
            return {
                "total": 1,
                "items": [{"commandId": f"cmd_{query['action'][0].replace('.', '_')}"}],
            }
        raise AssertionError(f"unexpected URL {url}")

    report = await acceptance_bundle_report(_acceptance_env(), fetch_json=fetch_json)

    assert report["status"] == "ready"
    assert report["summary"] == {"ready": 1, "degraded": 0, "blocked": 0}
    assert report["reports"]["remoteChromeE2E"]["status"] == "ready"

    output_path = tmp_path / "acceptance.json"
    write_acceptance_bundle_report(report, output_path)

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "ready"


@pytest.mark.asyncio
async def test_acceptance_bundle_is_blocked_when_required_evidence_is_missing():
    report = await acceptance_bundle_report({})

    assert report["status"] == "blocked"
    assert report["summary"]["blocked"] == 1
    assert report["reports"]["remoteChromeE2E"]["status"] == "blocked"
