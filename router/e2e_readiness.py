from __future__ import annotations

import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientSession


FetchJSON = Callable[[str, dict[str, str] | None], Awaitable[dict[str, Any]]]
Check = dict[str, str]


def _check(check_id: str, label: str, status: str, detail: str) -> Check:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _env_value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "") or "").strip()


def _normalize_url(raw: str) -> str:
    return raw.strip().rstrip("/")


def _http_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https"}


async def e2e_readiness_report(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: FetchJSON | None = None,
) -> dict[str, Any]:
    values = env if env is not None else os.environ
    web_ui_url = _normalize_url(_env_value(values, "HERMES_CHROME_WEB_UI_URL"))
    router_status_url = _normalize_url(_env_value(values, "HERMES_CHROME_ROUTER_STATUS_URL"))
    router_token = _env_value(values, "HERMES_CHROME_ROUTER_TOKEN")
    profile_id = _env_value(values, "HERMES_CHROME_E2E_PROFILE_ID")
    local_bridge_url = _normalize_url(
        _env_value(values, "HERMES_CHROME_LOCAL_BRIDGE_URL") or "http://127.0.0.1:16319"
    )
    checks: list[Check] = []

    required_failures = _required_checks(web_ui_url, router_status_url, router_token, profile_id)
    if required_failures:
        return _report(required_failures)

    fetch = fetch_json or _fetch_json
    checks.append(
        await _remote_json_check(
            "web-ui-update-manifest",
            "Remote web-ui Local Client update manifest",
            f"{web_ui_url}/api/devices/local-client/update",
            fetch,
            None,
            lambda payload: bool(payload),
            "remote web-ui update manifest is reachable.",
            "remote web-ui update manifest did not return JSON.",
        )
    )

    router_profile_payload: dict[str, Any] = {}
    try:
        router_profile_payload = await fetch(
            f"{router_status_url}/status/profile/{profile_id}",
            {"authorization": f"Bearer {router_token}"},
        )
        online = bool(router_profile_payload.get("online"))
        extension_connected = bool(router_profile_payload.get("extensionConnected"))
        if online and extension_connected:
            checks.append(
                _check(
                    "router-profile-status",
                    "Router profile status",
                    "pass",
                    f"profile {profile_id} is online on device {router_profile_payload.get('deviceId')}.",
                )
            )
        else:
            checks.append(
                _check(
                    "router-profile-status",
                    "Router profile status",
                    "fail",
                    f"profile online={online}, extensionConnected={extension_connected}.",
                )
            )
    except Exception as exc:
        checks.append(_check("router-profile-status", "Router profile status", "fail", str(exc)))

    tool_gate = router_profile_payload.get("toolGate") if isinstance(router_profile_payload, dict) else None
    if isinstance(tool_gate, dict) and (tool_gate.get("ready") is True or tool_gate.get("enabled") is True):
        checks.append(_check("router-tool-gate", "Router chrome_* tool gate", "pass", "chrome_* tool gate is ready."))
    else:
        reason = tool_gate.get("reason") if isinstance(tool_gate, dict) else "toolGate missing"
        checks.append(_check("router-tool-gate", "Router chrome_* tool gate", "fail", str(reason)))

    local_bridge_payload: dict[str, Any] = {}
    try:
        local_bridge_payload = await fetch(f"{local_bridge_url}/status", None)
        queued = int(local_bridge_payload.get("queuedCommands") or 0)
        pending = int(local_bridge_payload.get("pendingCommands") or 0)
        checks.append(
            _check(
                "local-bridge-status",
                "Local Client Browser Bridge status",
                "pass",
                f"local bridge reachable; queued={queued}, pending={pending}.",
            )
        )
    except Exception as exc:
        checks.append(_check("local-bridge-status", "Local Client Browser Bridge status", "fail", str(exc)))

    extension_connected = (
        bool(local_bridge_payload.get("connected") or local_bridge_payload.get("extensionConnected"))
        if isinstance(local_bridge_payload, dict)
        else False
    )
    if extension_connected:
        client_name = local_bridge_payload.get("clientName") or local_bridge_payload.get("extensionClientName") or "unknown"
        checks.append(
            _check(
                "local-extension-poll",
                "Local Chrome Extension poll",
                "pass",
                f"extension client={client_name} is polling.",
            )
        )
    else:
        checks.append(
            _check(
                "local-extension-poll",
                "Local Chrome Extension poll",
                "fail",
                "Hermes Chrome Extension is not polling the local bridge.",
            )
        )

    return _report(checks)


def _required_checks(web_ui_url: str, router_status_url: str, router_token: str, profile_id: str) -> list[Check]:
    checks: list[Check] = []
    if not web_ui_url or not _http_url(web_ui_url):
        checks.append(_check("web-ui-url", "Remote web-ui URL", "fail", "HERMES_CHROME_WEB_UI_URL must be http(s)."))
    if not router_status_url or not _http_url(router_status_url):
        checks.append(
            _check("router-status-url", "Router status URL", "fail", "HERMES_CHROME_ROUTER_STATUS_URL must be http(s).")
        )
    if not router_token:
        checks.append(_check("router-token", "Router token", "fail", "HERMES_CHROME_ROUTER_TOKEN is required."))
    if not profile_id:
        checks.append(_check("profile-id", "E2E profile id", "fail", "HERMES_CHROME_E2E_PROFILE_ID is required."))
    return checks


async def _remote_json_check(
    check_id: str,
    label: str,
    url: str,
    fetch_json: FetchJSON,
    headers: dict[str, str] | None,
    predicate,
    pass_detail: str,
    fail_detail: str,
) -> Check:
    try:
        payload = await fetch_json(url, headers)
        if predicate(payload):
            return _check(check_id, label, "pass", pass_detail)
        return _check(check_id, label, "fail", fail_detail)
    except Exception as exc:
        return _check(check_id, label, "fail", str(exc))


async def _fetch_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    async with ClientSession() as session:
        async with session.get(url, headers=headers or {}) as response:
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"GET {url} failed with {response.status}: {text[:200]}")
            payload = await response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"GET {url} did not return a JSON object")
            return payload


def _report(checks: list[Check]) -> dict[str, Any]:
    summary = {
        "pass": sum(1 for check in checks if check["status"] == "pass"),
        "warn": sum(1 for check in checks if check["status"] == "warn"),
        "fail": sum(1 for check in checks if check["status"] == "fail"),
    }
    return {
        "status": "blocked" if summary["fail"] else "degraded" if summary["warn"] else "ready",
        "summary": summary,
        "checks": checks,
    }


async def _main_async(_argv: Sequence[str] | None = None) -> int:
    report = await e2e_readiness_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_main_async(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
