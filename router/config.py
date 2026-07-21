from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RouterConfig:
    host: str
    port: int
    cloud_bridge_url: str
    router_token: str
    bindings: dict[str, str]
    binding_policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    bindings_path: Path | None = None
    web_ui_url: str = ""
    min_client_version: str = "0.1.0"
    rate_limit_per_profile: int = 10
    rate_limit_burst: int = 20
    audit_db_path: Path | None = None


def load_bindings(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values: dict[str, str] = {}
    for item in payload.get("bindings", []):
        profile_id = str(item.get("profileId") or "").strip()
        device_id = str(item.get("deviceId") or "").strip()
        if profile_id and device_id:
            values[profile_id] = device_id
    return values


def load_binding_policies(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values: dict[str, dict[str, Any]] = {}
    for item in payload.get("bindings", []):
        profile_id = str(item.get("profileId") or "").strip()
        action_policy = item.get("actionPolicy") or item.get("action_policy") or {}
        if profile_id and isinstance(action_policy, dict) and action_policy:
            values[profile_id] = dict(action_policy)
    return values


def load_config() -> RouterConfig:
    bindings_env = os.environ.get("HERMES_CHROME_BINDINGS_PATH", "").strip()
    bindings_path = Path(bindings_env).expanduser() if bindings_env else None
    web_ui_url = os.environ.get("HERMES_CHROME_WEB_UI_URL", "").strip().rstrip("/")
    return RouterConfig(
        host=os.environ.get("HERMES_CHROME_ROUTER_HOST", "127.0.0.1"),
        port=int(os.environ.get("HERMES_CHROME_ROUTER_PORT", "8787")),
        cloud_bridge_url=os.environ["HERMES_CHROME_CLOUD_BRIDGE_URL"].rstrip("/"),
        router_token=os.environ["HERMES_CHROME_ROUTER_TOKEN"],
        bindings_path=bindings_path,
        web_ui_url=web_ui_url,
        min_client_version=os.environ.get("HERMES_CHROME_MIN_CLIENT_VERSION", "0.1.0").strip() or "0.1.0",
        rate_limit_per_profile=int(os.environ.get("HERMES_CHROME_RATE_LIMIT_PER_USER", "10")),
        rate_limit_burst=int(os.environ.get("HERMES_CHROME_RATE_LIMIT_BURST", "20")),
        audit_db_path=Path(os.environ["HERMES_CHROME_ROUTER_AUDIT_DB"]).expanduser()
        if os.environ.get("HERMES_CHROME_ROUTER_AUDIT_DB", "").strip()
        else None,
        bindings=load_bindings(bindings_path) if bindings_path else {},
        binding_policies=load_binding_policies(bindings_path) if bindings_path else {},
    )
