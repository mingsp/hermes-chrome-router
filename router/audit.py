from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditCommandEvent:
    command_id: str
    profile_id: str
    action: str
    status: str
    device_id: str | None = None
    detail: str = ""
    params_hash: str = ""
    timestamp_ms: int = 0


class CommandAuditStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS command_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ms INTEGER NOT NULL,
                command_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                device_id TEXT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                params_hash TEXT NOT NULL
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_command_audit_profile ON command_audit_events(profile_id, timestamp_ms)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_command_audit_command ON command_audit_events(command_id, timestamp_ms)")
        self._db.commit()

    def record(
        self,
        *,
        command_id: str,
        profile_id: str,
        action: str,
        status: str,
        params: dict[str, Any] | None = None,
        device_id: str | None = None,
        detail: str = "",
    ) -> None:
        event = AuditCommandEvent(
            command_id=command_id,
            profile_id=profile_id,
            action=action,
            status=status,
            device_id=device_id,
            detail=detail,
            params_hash=params_hash(params or {}),
            timestamp_ms=int(time.time() * 1000),
        )
        self._db.execute(
            """
            INSERT INTO command_audit_events (
                timestamp_ms, command_id, profile_id, device_id, action, status, detail, params_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.timestamp_ms,
                event.command_id,
                event.profile_id,
                event.device_id,
                event.action,
                event.status,
                event.detail,
                event.params_hash,
            ),
        )
        self._db.commit()

    def search(
        self,
        *,
        profile_id: str = "",
        command_id: str = "",
        device_id: str = "",
        action: str = "",
        status: str = "",
        q: str = "",
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where, values = audit_where_clause(
            profile_id=profile_id,
            command_id=command_id,
            device_id=device_id,
            action=action,
            status=status,
            q=q,
            from_ms=from_ms,
            to_ms=to_ms,
        )
        capped_limit = max(1, min(limit, 500))
        capped_offset = max(0, offset)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM command_audit_events {where}",
            values,
        ).fetchone()[0]
        rows = self._db.execute(
            f"""
            SELECT timestamp_ms, command_id, profile_id, device_id, action, status, detail, params_hash
            FROM command_audit_events
            {where}
            ORDER BY timestamp_ms DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*values, capped_limit, capped_offset],
        ).fetchall()
        items = [row_to_item(row) for row in rows]
        return {
            "count": len(items),
            "total": total,
            "limit": capped_limit,
            "offset": capped_offset,
            "items": items,
        }

    def summary(
        self,
        *,
        profile_id: str = "",
        command_id: str = "",
        device_id: str = "",
        action: str = "",
        status: str = "",
        q: str = "",
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> dict[str, Any]:
        where, values = audit_where_clause(
            profile_id=profile_id,
            command_id=command_id,
            device_id=device_id,
            action=action,
            status=status,
            q=q,
            from_ms=from_ms,
            to_ms=to_ms,
        )
        totals = self._db.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                COUNT(DISTINCT profile_id) AS profiles,
                COUNT(DISTINCT device_id) AS devices
            FROM command_audit_events
            {where}
            """,
            values,
        ).fetchone()
        by_profile = self._db.execute(
            f"""
            SELECT
                profile_id,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM command_audit_events
            {where}
            GROUP BY profile_id
            ORDER BY total DESC, profile_id ASC
            LIMIT 25
            """,
            values,
        ).fetchall()
        by_status = self._db.execute(
            f"""
            SELECT status, COUNT(*) AS total
            FROM command_audit_events
            {where}
            GROUP BY status
            ORDER BY total DESC, status ASC
            """,
            values,
        ).fetchall()
        by_action = self._db.execute(
            f"""
            SELECT
                action,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM command_audit_events
            {where}
            GROUP BY action
            ORDER BY total DESC, action ASC
            LIMIT 25
            """,
            values,
        ).fetchall()
        return {
            "total": int(totals["total"] or 0),
            "failed": int(totals["failed"] or 0),
            "profiles": int(totals["profiles"] or 0),
            "devices": int(totals["devices"] or 0),
            "byProfile": [
                {
                    "profileId": str(row["profile_id"]),
                    "total": int(row["total"] or 0),
                    "failed": int(row["failed"] or 0),
                }
                for row in by_profile
            ],
            "byStatus": [
                {
                    "status": str(row["status"]),
                    "total": int(row["total"] or 0),
                }
                for row in by_status
            ],
            "byAction": [
                {
                    "action": str(row["action"]),
                    "total": int(row["total"] or 0),
                    "failed": int(row["failed"] or 0),
                }
                for row in by_action
            ],
        }

    def close(self) -> None:
        self._db.close()


def audit_where_clause(
    *,
    profile_id: str = "",
    command_id: str = "",
    device_id: str = "",
    action: str = "",
    status: str = "",
    q: str = "",
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if profile_id:
        clauses.append("profile_id = ?")
        values.append(profile_id)
    if command_id:
        clauses.append("command_id = ?")
        values.append(command_id)
    if device_id:
        clauses.append("device_id = ?")
        values.append(device_id)
    if action:
        clauses.append("action = ?")
        values.append(action)
    if status:
        clauses.append("status = ?")
        values.append(status)
    if q:
        clauses.append("(command_id LIKE ? OR profile_id LIKE ? OR action LIKE ? OR status LIKE ? OR detail LIKE ?)")
        like = f"%{q}%"
        values.extend([like, like, like, like, like])
    if from_ms is not None:
        clauses.append("timestamp_ms >= ?")
        values.append(max(0, int(from_ms)))
    if to_ms is not None:
        clauses.append("timestamp_ms <= ?")
        values.append(max(0, int(to_ms)))
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), values


def params_hash(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "timestampMs": row["timestamp_ms"],
        "commandId": row["command_id"],
        "profileId": row["profile_id"],
        "deviceId": row["device_id"],
        "action": row["action"],
        "status": row["status"],
        "detail": row["detail"],
        "paramsHash": row["params_hash"],
    }
