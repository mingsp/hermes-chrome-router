from __future__ import annotations

import json

from router.acceptance_archive import acceptance_archive_report


READY_JSON_FILES = (
    "local-client-installed-acceptance.json",
    "remote-chrome-e2e-readiness.json",
    "remote-chrome-e2e-acceptance.json",
    "chrome-router-acceptance.json",
)

def test_acceptance_archive_reports_ready_when_required_evidence_is_present(tmp_path):
    for filename in READY_JSON_FILES:
        (tmp_path / filename).write_text(json.dumps({"status": "ready"}) + "\n", encoding="utf-8")
    (tmp_path / "remote-chrome-e2e-from-ms.txt").write_text("1718500000000\n", encoding="utf-8")

    report = acceptance_archive_report(tmp_path)

    assert report["status"] == "ready"
    assert report["summary"] == {"pass": 5, "warn": 0, "fail": 0}
    assert {check["id"] for check in report["checks"] if check["status"] == "pass"} == {
        "json-local-client-installed-acceptance",
        "json-remote-chrome-e2e-readiness",
        "json-remote-chrome-e2e-acceptance",
        "json-chrome-router-acceptance",
        "text-remote-chrome-e2e-from-ms",
    }
    assert {check["id"] for check in report["checks"] if check["status"] == "warn"} == set()


def test_acceptance_archive_blocks_when_required_evidence_is_missing_or_not_ready(tmp_path):
    (tmp_path / "local-client-installed-acceptance.json").write_text('{"status":"blocked"}\n', encoding="utf-8")
    (tmp_path / "remote-chrome-e2e-from-ms.txt").write_text("\n", encoding="utf-8")

    report = acceptance_archive_report(tmp_path)

    assert report["status"] == "blocked"
    failed = {check["id"]: check for check in report["checks"] if check["status"] == "fail"}
    assert failed["json-local-client-installed-acceptance"]["detail"] == "status is blocked, expected ready."
    assert "missing" in failed["json-remote-chrome-e2e-readiness"]["detail"]
    assert "empty" in failed["text-remote-chrome-e2e-from-ms"]["detail"]
