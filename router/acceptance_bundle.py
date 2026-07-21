from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from router.e2e_acceptance import e2e_acceptance_report
from router.e2e_readiness import FetchJSON


async def acceptance_bundle_report(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: FetchJSON | None = None,
) -> dict[str, Any]:
    values = env if env is not None else os.environ
    e2e = await e2e_acceptance_report(values, fetch_json=fetch_json)
    reports = {
        "remoteChromeE2E": e2e,
    }
    summary = {
        "ready": sum(1 for report in reports.values() if report.get("status") == "ready"),
        "degraded": sum(1 for report in reports.values() if report.get("status") == "degraded"),
        "blocked": sum(1 for report in reports.values() if report.get("status") == "blocked"),
    }
    if summary["blocked"]:
        status = "blocked"
    elif summary["degraded"]:
        status = "degraded"
    else:
        status = "ready"
    return {
        "status": status,
        "summary": summary,
        "reports": reports,
    }


def write_acceptance_bundle_report(report: Mapping[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _main_async(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    output_path = _output_path(args)
    report = await acceptance_bundle_report()
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


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_main_async(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
