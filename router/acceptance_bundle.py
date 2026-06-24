from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from router.e2e_acceptance import e2e_acceptance_report
from router.e2e_readiness import FetchJSON
from router.gateway_acceptance import gateway_acceptance_report


async def acceptance_bundle_report(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: FetchJSON | None = None,
    require_gateway_failover: bool | None = None,
) -> dict[str, Any]:
    values = env if env is not None else os.environ
    require_gateway = (
        require_gateway_failover
        if require_gateway_failover is not None
        else _env_bool(values.get("HERMES_CHROME_ACCEPTANCE_REQUIRE_GATEWAY_FAILOVER", ""))
    )
    e2e = await e2e_acceptance_report(values, fetch_json=fetch_json)
    reports = {
        "remoteChromeE2E": e2e,
    }
    optional_reports: dict[str, Any] = {}
    if require_gateway:
        reports["gatewayFailover"] = await gateway_acceptance_report(values, fetch_json=fetch_json)
    else:
        optional_reports["gatewayFailover"] = {
            "status": "skipped",
            "detail": "Gateway failover evidence is optional for internal small-team acceptance. Pass --require-gateway-failover to enforce it.",
        }
    summary = {
        "ready": sum(1 for report in reports.values() if report.get("status") == "ready"),
        "degraded": sum(1 for report in reports.values() if report.get("status") == "degraded"),
        "blocked": sum(1 for report in reports.values() if report.get("status") == "blocked"),
        "skipped": sum(1 for report in optional_reports.values() if report.get("status") == "skipped"),
    }
    if summary["blocked"]:
        status = "blocked"
    elif summary["degraded"]:
        status = "degraded"
    else:
        status = "ready"
    return {
        "status": status,
        "requireGatewayFailover": require_gateway,
        "summary": summary,
        "reports": reports,
        "optionalReports": optional_reports,
    }


def write_acceptance_bundle_report(report: Mapping[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _main_async(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    output_path = _output_path(args)
    report = await acceptance_bundle_report(require_gateway_failover=_require_gateway_failover(args))
    if output_path:
        write_acceptance_bundle_report(report, output_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


def _output_path(args: list[str]) -> str:
    for index, value in enumerate(args):
        if value == "--output" and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("--output="):
            return value.split("=", 1)[1]
    return ""


def _require_gateway_failover(args: list[str]) -> bool | None:
    if "--require-gateway-failover" in args:
        return True
    return None


def _env_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_main_async(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
