from __future__ import annotations

from router.e2e_acceptance import e2e_acceptance_report


async def test_e2e_acceptance_reports_ready_when_required_actions_completed_in_router_audit():
    calls = []

    async def fetch_json(url, headers=None):
        calls.append((url, headers or {}))
        if url == "https://hermes.example.com/api/devices/local-client/update":
            return {"version": "0.2.0"}
        if url == "https://router.example.com/status/profile/xuxiaofeng_profile":
            assert headers == {"authorization": "Bearer router-token"}
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": True,
                "extensionConnected": True,
                "toolGate": {"ready": True},
            }
        if url == "http://127.0.0.1:16319/status":
            return {
                "connected": True,
                "clientName": "hermes-extension",
                "queuedCommands": 0,
                "pendingCommands": 0,
            }
        if url == (
            "https://router.example.com/audit/commands"
            "?profileId=xuxiaofeng_profile&action=tab.list&status=completed&fromMs=1718500000000&limit=10"
        ):
            assert headers == {"authorization": "Bearer router-token"}
            return {"total": 1, "items": [{"commandId": "cmd_tab", "action": "tab.list", "status": "completed"}]}
        if url == (
            "https://router.example.com/audit/commands"
            "?profileId=xuxiaofeng_profile&action=page.snapshot&status=completed&fromMs=1718500000000&limit=10"
        ):
            return {
                "total": 1,
                "items": [{"commandId": "cmd_snapshot", "action": "page.snapshot", "status": "completed"}],
            }
        if url == (
            "https://router.example.com/audit/commands"
            "?profileId=xuxiaofeng_profile&action=page.click&status=completed&fromMs=1718500000000&limit=10"
        ):
            return {"total": 1, "items": [{"commandId": "cmd_click", "action": "page.click", "status": "completed"}]}
        raise AssertionError(f"unexpected URL {url}")

    report = await e2e_acceptance_report(
        {
            "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
            "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
            "HERMES_CHROME_ROUTER_TOKEN": "router-token",
            "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
            "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
            "HERMES_CHROME_E2E_FROM_MS": "1718500000000",
        },
        fetch_json=fetch_json,
    )

    assert report["status"] == "ready"
    assert report["readiness"]["status"] == "ready"
    assert report["summary"] == {"pass": 8, "warn": 0, "fail": 0}
    assert {check["id"] for check in report["checks"] if check["id"].startswith("audit-action-")} == {
        "audit-action-tab-list",
        "audit-action-page-snapshot",
        "audit-action-page-click",
    }
    assert calls[-1][1] == {"authorization": "Bearer router-token"}


async def test_e2e_acceptance_blocks_when_required_audit_action_is_missing():
    async def fetch_json(url, headers=None):
        if url == "https://hermes.example.com/api/devices/local-client/update":
            return {"version": "0.2.0"}
        if url == "https://router.example.com/status/profile/xuxiaofeng_profile":
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": True,
                "extensionConnected": True,
                "toolGate": {"ready": True},
            }
        if url == "http://127.0.0.1:16319/status":
            return {"connected": True, "clientName": "hermes-extension", "queuedCommands": 0, "pendingCommands": 0}
        if url.startswith("https://router.example.com/audit/commands"):
            return {"total": 0, "items": []}
        raise AssertionError(f"unexpected URL {url}")

    report = await e2e_acceptance_report(
        {
            "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
            "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
            "HERMES_CHROME_ROUTER_TOKEN": "router-token",
            "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
            "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
        },
        fetch_json=fetch_json,
    )

    assert report["status"] == "blocked"
    failed = {check["id"]: check for check in report["checks"] if check["status"] == "fail"}
    assert "audit-action-tab-list" in failed
    assert "audit-action-page-snapshot" in failed
    assert "audit-action-page-click" in failed

