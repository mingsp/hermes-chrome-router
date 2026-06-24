from __future__ import annotations

from router.e2e_readiness import e2e_readiness_report


async def test_e2e_readiness_reports_ready_when_remote_router_local_bridge_and_extension_are_ready():
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
        raise AssertionError(f"unexpected URL {url}")

    report = await e2e_readiness_report(
        {
            "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
            "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
            "HERMES_CHROME_ROUTER_TOKEN": "router-token",
            "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
            "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
        },
        fetch_json=fetch_json,
    )

    assert report["status"] == "ready"
    assert report["summary"] == {"pass": 5, "warn": 0, "fail": 0}
    assert [check["id"] for check in report["checks"]] == [
        "web-ui-update-manifest",
        "router-profile-status",
        "router-tool-gate",
        "local-bridge-status",
        "local-extension-poll",
    ]
    assert calls[1][1] == {"authorization": "Bearer router-token"}


async def test_e2e_readiness_accepts_wails_local_client_status_shape():
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
            return {
                "status": "listening",
                "extensionConnected": True,
                "extensionClientName": "Hermes Chrome Connector annpkkedhmeediplaggpmhdfkoibfmel",
                "queuedCommands": 0,
                "expectedExtensionVersion": "0.15.38",
            }
        raise AssertionError(f"unexpected URL {url}")

    report = await e2e_readiness_report(
        {
            "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
            "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
            "HERMES_CHROME_ROUTER_TOKEN": "router-token",
            "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
            "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
        },
        fetch_json=fetch_json,
    )

    assert report["status"] == "ready"
    extension_check = next(check for check in report["checks"] if check["id"] == "local-extension-poll")
    assert extension_check["status"] == "pass"
    assert "Hermes Chrome Connector" in extension_check["detail"]


async def test_e2e_readiness_accepts_router_tool_gate_enabled_shape():
    async def fetch_json(url, headers=None):
        if url == "https://hermes.example.com/api/devices/local-client/update":
            return {"version": "0.2.0"}
        if url == "https://router.example.com/status/profile/xuxiaofeng_profile":
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": True,
                "extensionConnected": True,
                "toolGate": {
                    "enabled": True,
                    "reason": "profile browser binding and Local Client are ready",
                },
            }
        if url == "http://127.0.0.1:16319/status":
            return {
                "status": "listening",
                "extensionConnected": True,
                "extensionClientName": "Hermes Chrome Connector annpkkedhmeediplaggpmhdfkoibfmel",
                "queuedCommands": 0,
                "expectedExtensionVersion": "0.15.38",
            }
        raise AssertionError(f"unexpected URL {url}")

    report = await e2e_readiness_report(
        {
            "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
            "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
            "HERMES_CHROME_ROUTER_TOKEN": "router-token",
            "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
            "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
        },
        fetch_json=fetch_json,
    )

    assert report["status"] == "ready"
    tool_gate_check = next(check for check in report["checks"] if check["id"] == "router-tool-gate")
    assert tool_gate_check["status"] == "pass"


async def test_e2e_readiness_blocks_when_required_environment_is_missing():
    async def fetch_json(_url, headers=None):
        raise AssertionError("network should not be called when required environment is missing")

    report = await e2e_readiness_report({}, fetch_json=fetch_json)

    assert report["status"] == "blocked"
    failed = {check["id"]: check for check in report["checks"] if check["status"] == "fail"}
    assert "web-ui-url" in failed
    assert "router-status-url" in failed
    assert "router-token" in failed
    assert "profile-id" in failed


async def test_e2e_readiness_blocks_when_profile_or_extension_is_not_ready():
    async def fetch_json(url, headers=None):
        if url == "https://hermes.example.com/api/devices/local-client/update":
            return {"version": "0.2.0"}
        if url == "https://router.example.com/status/profile/xuxiaofeng_profile":
            return {
                "profileId": "xuxiaofeng_profile",
                "deviceId": "span-macbook",
                "online": False,
                "extensionConnected": False,
                "toolGate": {"ready": False, "reason": "Hermes Local Client is not connected"},
            }
        if url == "http://127.0.0.1:16319/status":
            return {"connected": False, "clientName": None, "queuedCommands": 0, "pendingCommands": 0}
        raise AssertionError(f"unexpected URL {url}")

    report = await e2e_readiness_report(
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
    assert "router-profile-status" in failed
    assert "router-tool-gate" in failed
    assert "local-extension-poll" in failed
