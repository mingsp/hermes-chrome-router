from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from router.remote_acceptance_run import _archive_dir, run_remote_acceptance_phase


def _env() -> dict[str, str]:
    return {
        "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
        "HERMES_CHROME_ROUTER_STATUS_URL": "https://router.example.com",
        "HERMES_CHROME_ROUTER_TOKEN": "router-token",
        "HERMES_CHROME_E2E_PROFILE_ID": "xuxiaofeng_profile",
        "HERMES_CHROME_LOCAL_BRIDGE_URL": "http://127.0.0.1:16319",
    }


async def _fetch_json(url: str, headers: dict[str, str] | None = None):
    parsed = urlparse(url)
    if url == "https://hermes.example.com/api/devices/local-client/update":
        return {"version": "0.1.0"}
    if url == "http://127.0.0.1:16319/status":
        return {
            "connected": True,
            "clientName": "Hermes Chrome Connector",
            "queuedCommands": 0,
            "pendingCommands": 0,
        }
    if parsed.path == "/status/profile/xuxiaofeng_profile":
        assert headers == {"authorization": "Bearer router-token"}
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


@pytest.mark.asyncio
async def test_remote_acceptance_readiness_phase_writes_starting_evidence(tmp_path):
    report = await run_remote_acceptance_phase(
        "readiness",
        archive_dir=tmp_path,
        env=_env(),
        fetch_json=_fetch_json,
        now_ms=lambda: 1718500000000,
    )

    assert report["status"] == "ready"
    assert report["phase"] == "readiness"
    assert report["nextStep"] == "run real Mac Chrome actions, then run phase=collect"
    assert json.loads((tmp_path / "remote-chrome-e2e-readiness.json").read_text(encoding="utf-8"))["status"] == "ready"
    assert (tmp_path / "remote-chrome-e2e-from-ms.txt").read_text(encoding="utf-8").strip() == "1718500000000"


@pytest.mark.asyncio
async def test_remote_acceptance_collect_phase_writes_router_reports_and_archive(tmp_path):
    (tmp_path / "local-client-installed-acceptance.json").write_text('{"status":"ready"}\n', encoding="utf-8")
    (tmp_path / "remote-chrome-e2e-readiness.json").write_text('{"status":"ready"}\n', encoding="utf-8")
    (tmp_path / "remote-chrome-e2e-from-ms.txt").write_text("1718500000000\n", encoding="utf-8")

    report = await run_remote_acceptance_phase(
        "collect",
        archive_dir=tmp_path,
        env=_env(),
        fetch_json=_fetch_json,
    )

    assert report["status"] == "ready"
    assert report["phase"] == "collect"
    assert json.loads((tmp_path / "remote-chrome-e2e-acceptance.json").read_text(encoding="utf-8"))["status"] == "ready"
    assert json.loads((tmp_path / "chrome-router-acceptance.json").read_text(encoding="utf-8"))["status"] == "ready"
    assert report["archive"]["status"] == "ready"


def test_remote_acceptance_cli_accepts_output_dir_alias():
    assert _archive_dir(["--output-dir", "/tmp/hermes-acceptance"]) == "/tmp/hermes-acceptance"
    assert _archive_dir(["--output-dir=/tmp/hermes-acceptance"]) == "/tmp/hermes-acceptance"
