from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class LocalClientAuthorization:
    server_url: str
    router_url: str
    device_id: str
    token: str
    profile_ids: tuple[str, ...]


def default_config_dir() -> Path:
    configured = os.environ.get("HERMES_CHROME_LOCAL_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".hermes-chrome-router-harness"


def _clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def _clean_required(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} is required")
    return cleaned


def _clean_profile_ids(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(item.strip() for item in values if item.strip())


class LocalClientAuthConfigStore:
    def __init__(self, config_dir: Path | str | None = None):
        self.config_dir = Path(config_dir).expanduser() if config_dir is not None else default_config_dir()
        self.config_path = self.config_dir / "config.json"
        self.token_path = self.config_dir / "token"

    def save_authorization(
        self,
        *,
        server_url: str,
        router_url: str,
        device_id: str,
        token: str,
        profile_ids: Iterable[str],
    ) -> LocalClientAuthorization:
        authorization = LocalClientAuthorization(
            server_url=_clean_url(_clean_required(server_url, "server_url")),
            router_url=_clean_required(router_url, "router_url"),
            device_id=_clean_required(device_id, "device_id"),
            token=_clean_required(token, "token"),
            profile_ids=_clean_profile_ids(profile_ids),
        )
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config_payload = {
            "serverUrl": authorization.server_url,
            "routerUrl": authorization.router_url,
            "deviceId": authorization.device_id,
            "profileIds": list(authorization.profile_ids),
        }
        self.config_path.write_text(
            json.dumps(config_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.token_path.write_text(authorization.token, encoding="utf-8")
        os.chmod(self.token_path, 0o600)
        return authorization

    def load_authorization(self) -> LocalClientAuthorization | None:
        if not self.config_path.exists() or not self.token_path.exists():
            return None
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            token = self.token_path.read_text(encoding="utf-8").strip()
            profile_ids = payload.get("profileIds") or []
            if not isinstance(profile_ids, list):
                profile_ids = []
            return LocalClientAuthorization(
                server_url=_clean_url(_clean_required(str(payload.get("serverUrl") or ""), "server_url")),
                router_url=_clean_required(str(payload.get("routerUrl") or ""), "router_url"),
                device_id=_clean_required(str(payload.get("deviceId") or ""), "device_id"),
                token=_clean_required(token, "token"),
                profile_ids=_clean_profile_ids(str(item) for item in profile_ids),
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
