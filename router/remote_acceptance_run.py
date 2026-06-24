from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from router.acceptance_archive import acceptance_archive_report
from router.acceptance_bundle import acceptance_bundle_report, write_acceptance_bundle_report
from router.e2e_acceptance import e2e_acceptance_report
from router.e2e_readiness import FetchJSON, e2e_readiness_report


Phase = str
NowMS = Callable[[], int]


async def run_remote_acceptance_phase(
    phase: Phase,
    *,
    archive_dir: str | Path,
    env: Mapping[str, str] | None = None,
    fetch_json: FetchJSON | None = None,
    now_ms: NowMS | None = None,
    require_gateway_failover: bool = False,
) -> dict[str, Any]:
    root = Path(archive_dir)
    root.mkdir(parents=True, exist_ok=True)
    values: dict[str, str] = dict(os.environ if env is None else env)

    if phase == "readiness":
        readiness = await e2e_readiness_report(values, fetch_json=fetch_json)
        _write_json(root / "remote-chrome-e2e-readiness.json", readiness)
        start_ms = _env_or_now_ms(values, now_ms)
        (root / "remote-chrome-e2e-from-ms.txt").write_text(f"{start_ms}\n", encoding="utf-8")
        return {
            "status": readiness["status"],
            "phase": "readiness",
            "archiveDir": str(root),
            "reports": {"remoteChromeE2EReadiness": readiness},
            "nextStep": "run real Mac Chrome actions, then run phase=collect",
        }

    if phase == "collect":
        values.setdefault("HERMES_CHROME_E2E_FROM_MS", _read_from_ms(root))
        acceptance = await e2e_acceptance_report(values, fetch_json=fetch_json)
        _write_json(root / "remote-chrome-e2e-acceptance.json", acceptance)
        bundle = await acceptance_bundle_report(
            values,
            fetch_json=fetch_json,
            require_gateway_failover=require_gateway_failover,
        )
        write_acceptance_bundle_report(bundle, root / "chrome-router-acceptance.json")
        archive = acceptance_archive_report(root, require_gateway_failover=require_gateway_failover)
        return {
            "status": archive["status"],
            "phase": "collect",
            "archiveDir": str(root),
            "reports": {
                "remoteChromeE2EAcceptance": acceptance,
                "chromeRouterAcceptance": bundle,
            },
            "archive": archive,
        }

    raise ValueError("phase must be readiness or collect")


def _env_or_now_ms(env: Mapping[str, str], now_ms: NowMS | None) -> int:
    raw = str(env.get("HERMES_CHROME_E2E_FROM_MS") or "").strip()
    if raw:
        return int(raw)
    if now_ms is not None:
        return int(now_ms())
    return int(time.time() * 1000)


def _read_from_ms(root: Path) -> str:
    return (root / "remote-chrome-e2e-from-ms.txt").read_text(encoding="utf-8").strip()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _main_async(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    report = await run_remote_acceptance_phase(
        _phase(args),
        archive_dir=_archive_dir(args),
        require_gateway_failover=_require_gateway_failover(args),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


def _phase(args: list[str]) -> str:
    for index, value in enumerate(args):
        if value == "--phase" and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("--phase="):
            return value.split("=", 1)[1]
    return "readiness"


def _archive_dir(args: list[str]) -> str:
    archive_flags = {"--archive-dir", "--output-dir"}
    for index, value in enumerate(args):
        if value in archive_flags and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("--archive-dir=") or value.startswith("--output-dir="):
            return value.split("=", 1)[1]
    return "./artifacts/hermes-chrome-acceptance"


def _require_gateway_failover(args: list[str]) -> bool:
    return "--require-gateway-failover" in args


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_main_async(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
