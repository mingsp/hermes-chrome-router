from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from router.e2e_readiness import FetchJSON
from router.gateway_readiness import gateway_readiness_report

DEFAULT_ACTIONS = ("tab.list",)


def _env_value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "") or "").strip()


def _normalize_url(raw: str) -> str:
    return raw.strip().rstrip("/")


def _check(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _required_actions(env: Mapping[str, str]) -> list[str]:
    raw = _env_value(env, "HERMES_CHROME_FAILOVER_REQUIRED_ACTIONS")
    if not raw:
        return list(DEFAULT_ACTIONS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _action_check_id(action: str) -> str:
    return f"failover-audit-action-{action.replace('.', '-')}"


async def gateway_acceptance_report(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: FetchJSON | None = None,
) -> dict[str, Any]:
    values = env if env is not None else os.environ
    readiness = gateway_readiness_report(values)
    checks: list[dict[str, str]] = [*readiness.get("checks", [])]

    if readiness.get("status") == "blocked":
        return _report(checks, readiness)

    router_status_url = _normalize_url(_env_value(values, "HERMES_CHROME_ROUTER_STATUS_URL"))
    router_token = _env_value(values, "HERMES_CHROME_ROUTER_TOKEN")
    profile_id = _env_value(values, "HERMES_CHROME_FAILOVER_PROFILE_ID")
    expected_server_id = _env_value(values, "HERMES_CHROME_FAILOVER_EXPECTED_SERVER_ID")
    expected_device_id = _env_value(values, "HERMES_CHROME_FAILOVER_EXPECTED_DEVICE_ID")
    from_ms = _env_value(values, "HERMES_CHROME_FAILOVER_FROM_MS")

    missing = []
    if not router_status_url:
        missing.append("HERMES_CHROME_ROUTER_STATUS_URL")
    if not router_token:
        missing.append("HERMES_CHROME_ROUTER_TOKEN")
    if not profile_id:
        missing.append("HERMES_CHROME_FAILOVER_PROFILE_ID")
    if not expected_server_id:
        missing.append("HERMES_CHROME_FAILOVER_EXPECTED_SERVER_ID")
    if missing:
        checks.append(
            _check(
                "failover-evidence-config",
                "Gateway failover evidence configuration",
                "fail",
                f"missing required env: {', '.join(missing)}.",
            )
        )
        return _report(checks, readiness)

    fetch = fetch_json
    if fetch is None:
        from router.e2e_readiness import _fetch_json

        fetch = _fetch_json

    headers = {"authorization": f"Bearer {router_token}"}
    try:
        status = await fetch(f"{router_status_url}/status", headers)
        server_id = str(status.get("serverId") or "").strip()
        if server_id == expected_server_id:
            checks.append(
                _check(
                    "failover-router-server",
                    "Gateway failover active Router",
                    "pass",
                    f"target Router reports serverId {server_id}.",
                )
            )
        else:
            checks.append(
                _check(
                    "failover-router-server",
                    "Gateway failover active Router",
                    "fail",
                    f"expected serverId {expected_server_id}, got {server_id or '<missing>'}.",
                )
            )
    except Exception as exc:
        checks.append(_check("failover-router-server", "Gateway failover active Router", "fail", str(exc)))

    try:
        profile = await fetch(f"{router_status_url}/status/profile/{profile_id}", headers)
        online = bool(profile.get("online"))
        extension_connected = bool(profile.get("extensionConnected"))
        device_id = str(profile.get("deviceId") or "").strip()
        expected_device_matches = not expected_device_id or device_id == expected_device_id
        if online and extension_connected and expected_device_matches:
            checks.append(
                _check(
                    "failover-profile-online",
                    "Gateway failover profile online",
                    "pass",
                    f"profile {profile_id} is online on device {device_id}.",
                )
            )
        else:
            checks.append(
                _check(
                    "failover-profile-online",
                    "Gateway failover profile online",
                    "fail",
                    (
                        f"profile online={online}, extensionConnected={extension_connected}, "
                        f"deviceId={device_id or '<missing>'}."
                    ),
                )
            )
    except Exception as exc:
        checks.append(_check("failover-profile-online", "Gateway failover profile online", "fail", str(exc)))

    for action in _required_actions(values):
        query = {
            "profileId": profile_id,
            "action": action,
            "status": "completed",
            "limit": "10",
        }
        if from_ms:
            query["fromMs"] = from_ms
        try:
            payload = await fetch(f"{router_status_url}/audit/commands?{urlencode(query)}", headers)
            total = int(payload.get("total") or len(payload.get("items") or []))
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            command_ids = [str(item.get("commandId") or "") for item in items if isinstance(item, dict)]
            if total > 0:
                checks.append(
                    _check(
                        _action_check_id(action),
                        f"Gateway failover completed action {action}",
                        "pass",
                        f"found {total} completed command(s): {', '.join(command_ids[:3]) or 'command id unavailable'}.",
                    )
                )
            else:
                checks.append(
                    _check(
                        _action_check_id(action),
                        f"Gateway failover completed action {action}",
                        "fail",
                        f"no completed {action} command found for profile {profile_id} after the failover drill.",
                    )
                )
        except Exception as exc:
            checks.append(_check(_action_check_id(action), f"Gateway failover completed action {action}", "fail", str(exc)))

    return _report(checks, readiness)


def _report(checks: list[dict[str, str]], readiness: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "pass": sum(1 for check in checks if check["status"] == "pass"),
        "warn": sum(1 for check in checks if check["status"] == "warn"),
        "fail": sum(1 for check in checks if check["status"] == "fail"),
    }
    return {
        "status": "blocked" if summary["fail"] else "degraded" if summary["warn"] else "ready",
        "summary": summary,
        "readiness": readiness,
        "checks": checks,
    }


async def _main_async(_argv: Sequence[str] | None = None) -> int:
    report = await gateway_acceptance_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_main_async(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
