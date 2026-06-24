from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from router.e2e_readiness import FetchJSON, e2e_readiness_report


DEFAULT_ACTIONS = ("tab.list", "page.snapshot", "page.click")


def _env_value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "") or "").strip()


def _normalize_url(raw: str) -> str:
    return raw.strip().rstrip("/")


def _action_check_id(action: str) -> str:
    return f"audit-action-{action.replace('.', '-')}"


def _check(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _required_actions(env: Mapping[str, str]) -> list[str]:
    raw = _env_value(env, "HERMES_CHROME_E2E_REQUIRED_ACTIONS")
    if not raw:
        return list(DEFAULT_ACTIONS)
    return [item.strip() for item in raw.split(",") if item.strip()]


async def e2e_acceptance_report(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: FetchJSON | None = None,
) -> dict[str, Any]:
    values = env if env is not None else os.environ
    router_status_url = _normalize_url(_env_value(values, "HERMES_CHROME_ROUTER_STATUS_URL"))
    router_token = _env_value(values, "HERMES_CHROME_ROUTER_TOKEN")
    profile_id = _env_value(values, "HERMES_CHROME_E2E_PROFILE_ID")
    from_ms = _env_value(values, "HERMES_CHROME_E2E_FROM_MS")

    readiness = await e2e_readiness_report(values, fetch_json=fetch_json)
    checks: list[dict[str, str]] = [*readiness.get("checks", [])]

    if readiness.get("status") != "ready":
        return _report(checks, readiness)

    if not router_status_url or not router_token or not profile_id:
        checks.append(
            _check(
                "audit-evidence-config",
                "Router audit evidence configuration",
                "fail",
                "HERMES_CHROME_ROUTER_STATUS_URL, HERMES_CHROME_ROUTER_TOKEN, and HERMES_CHROME_E2E_PROFILE_ID are required.",
            )
        )
        return _report(checks, readiness)

    fetch = fetch_json
    if fetch is None:
        from router.e2e_readiness import _fetch_json

        fetch = _fetch_json

    headers = {"authorization": f"Bearer {router_token}"}
    for action in _required_actions(values):
        query = {
            "profileId": profile_id,
            "action": action,
            "status": "completed",
        }
        if from_ms:
            query["fromMs"] = from_ms
        query["limit"] = "10"
        url = f"{router_status_url}/audit/commands?{urlencode(query)}"
        try:
            payload = await fetch(url, headers)
            total = int(payload.get("total") or len(payload.get("items") or []))
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            command_ids = [str(item.get("commandId") or "") for item in items if isinstance(item, dict)]
            if total > 0:
                checks.append(
                    _check(
                        _action_check_id(action),
                        f"Router audit completed action {action}",
                        "pass",
                        f"found {total} completed command(s): {', '.join(command_ids[:3]) or 'command id unavailable'}.",
                    )
                )
            else:
                checks.append(
                    _check(
                        _action_check_id(action),
                        f"Router audit completed action {action}",
                        "fail",
                        f"no completed {action} command found for profile {profile_id}.",
                    )
                )
        except Exception as exc:
            checks.append(_check(_action_check_id(action), f"Router audit completed action {action}", "fail", str(exc)))

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
    report = await e2e_acceptance_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_main_async(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
