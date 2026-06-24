from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


REQUIRED_READY_JSON = (
    "local-client-installed-acceptance.json",
    "remote-chrome-e2e-readiness.json",
    "remote-chrome-e2e-acceptance.json",
    "chrome-router-acceptance.json",
)

REQUIRED_TEXT = (
    "remote-chrome-e2e-from-ms.txt",
)

GATEWAY_READY_JSON = (
    "gateway-readiness.json",
    "gateway-failover-acceptance.json",
)

GATEWAY_TEXT = (
    "gateway-failover-from-ms.txt",
    "gateway-failover-drill.md",
)


def acceptance_archive_report(archive_dir: str | Path, *, require_gateway_failover: bool = False) -> dict[str, Any]:
    root = Path(archive_dir)
    checks: list[dict[str, str]] = []
    for filename in REQUIRED_READY_JSON:
        checks.append(_json_status_check(root / filename))
    for filename in REQUIRED_TEXT:
        checks.append(_text_file_check(root / filename))
    for filename in GATEWAY_READY_JSON:
        checks.append(_json_status_check(root / filename) if require_gateway_failover else _optional_json_status_check(root / filename))
    for filename in GATEWAY_TEXT:
        checks.append(_text_file_check(root / filename) if require_gateway_failover else _optional_text_file_check(root / filename))
    summary = {
        "pass": sum(1 for check in checks if check["status"] == "pass"),
        "warn": sum(1 for check in checks if check["status"] == "warn"),
        "fail": sum(1 for check in checks if check["status"] == "fail"),
    }
    return {
        "status": "blocked" if summary["fail"] else "ready",
        "archiveDir": str(root),
        "requireGatewayFailover": require_gateway_failover,
        "summary": summary,
        "checks": checks,
    }


def _json_status_check(path: Path) -> dict[str, str]:
    check_id = f"json-{path.stem}"
    label = f"Acceptance JSON {path.name}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _check(check_id, label, "fail", f"{path} is missing.")
    except json.JSONDecodeError as exc:
        return _check(check_id, label, "fail", f"invalid JSON: {exc.msg}.")
    status = str(payload.get("status") or "").strip()
    if status == "ready":
        return _check(check_id, label, "pass", f"{path} is ready.")
    return _check(check_id, label, "fail", f"status is {status or '<missing>'}, expected ready.")


def _text_file_check(path: Path) -> dict[str, str]:
    check_id = f"text-{path.stem}"
    label = f"Acceptance evidence {path.name}"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return _check(check_id, label, "fail", f"{path} is missing.")
    if not content:
        return _check(check_id, label, "fail", f"{path} is empty.")
    return _check(check_id, label, "pass", f"{path} is present.")


def _optional_json_status_check(path: Path) -> dict[str, str]:
    required = _json_status_check(path)
    if required["status"] == "pass":
        return required
    return _check(
        required["id"],
        required["label"],
        "warn",
        f"{required['detail']} Gateway failover evidence is optional for internal small-team acceptance.",
    )


def _optional_text_file_check(path: Path) -> dict[str, str]:
    required = _text_file_check(path)
    if required["status"] == "pass":
        return required
    return _check(
        required["id"],
        required["label"],
        "warn",
        f"{required['detail']} Gateway failover evidence is optional for internal small-team acceptance.",
    )


def _check(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    archive_dir = _archive_dir(args)
    report = acceptance_archive_report(archive_dir, require_gateway_failover=_require_gateway_failover(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


def _archive_dir(args: list[str]) -> str:
    for index, value in enumerate(args):
        if value == "--archive-dir" and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("--archive-dir="):
            return value.split("=", 1)[1]
    if args and not args[0].startswith("--"):
        return args[0]
    return "./artifacts/hermes-chrome-acceptance"


def _require_gateway_failover(args: list[str]) -> bool:
    if "--require-gateway-failover" in args:
        return True
    raw = os.environ.get("HERMES_CHROME_ACCEPTANCE_REQUIRE_GATEWAY_FAILOVER", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
