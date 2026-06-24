from __future__ import annotations

import os
from dataclasses import dataclass

from harness.auth_config import LocalClientAuthConfigStore


@dataclass(frozen=True)
class LocalClientConfig:
    router_url: str
    router_token: str
    device_id: str
    profile_ids: tuple[str, ...]
    local_host: str
    local_port: int


def load_config() -> LocalClientConfig:
    if (
        os.environ.get("HERMES_CHROME_ROUTER_URL")
        and os.environ.get("HERMES_CHROME_ROUTER_TOKEN")
        and os.environ.get("HERMES_CHROME_DEVICE_ID")
    ):
        return _load_env_config()

    authorization = LocalClientAuthConfigStore().load_authorization()
    if authorization is None:
        return _load_env_config()

    return LocalClientConfig(
        router_url=authorization.router_url,
        router_token=authorization.token,
        device_id=authorization.device_id,
        profile_ids=authorization.profile_ids,
        local_host=os.environ.get("HERMES_CHROME_LOCAL_HOST", "127.0.0.1"),
        local_port=int(os.environ.get("HERMES_CHROME_LOCAL_PORT", "16319")),
    )


def _load_env_config() -> LocalClientConfig:
    profile_ids = tuple(
        item.strip()
        for item in os.environ.get("HERMES_CHROME_PROFILE_IDS", "").split(",")
        if item.strip()
    )
    return LocalClientConfig(
        router_url=os.environ["HERMES_CHROME_ROUTER_URL"],
        router_token=os.environ["HERMES_CHROME_ROUTER_TOKEN"],
        device_id=os.environ["HERMES_CHROME_DEVICE_ID"],
        profile_ids=profile_ids,
        local_host=os.environ.get("HERMES_CHROME_LOCAL_HOST", "127.0.0.1"),
        local_port=int(os.environ.get("HERMES_CHROME_LOCAL_PORT", "16319")),
    )
