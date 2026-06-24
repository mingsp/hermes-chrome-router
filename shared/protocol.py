from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ProtocolError


@dataclass(frozen=True)
class CloudCommand:
    id: str
    profile_id: str
    action: str
    params: dict[str, Any]
    expected_extension_version: str | None = None


@dataclass(frozen=True)
class HelloFrame:
    device_id: str
    token: str
    profile_ids: tuple[str, ...]
    client_version: str


@dataclass(frozen=True)
class CommandFrame:
    id: str
    profile_id: str
    action: str
    params: dict[str, Any]
    timeout_ms: int


@dataclass(frozen=True)
class ResultFrame:
    id: str
    profile_id: str
    ok: bool
    result: Any = None
    error: str | None = None


@dataclass(frozen=True)
class HeartbeatFrame:
    timestamp: int
    extension_connected: bool
    extension_version: str | None


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"missing or invalid {key}")
    return value.strip()


def parse_cloud_next_payload(payload: dict[str, Any]) -> CloudCommand | None:
    payload_type = payload.get("type")
    if payload_type == "none":
        return None
    if payload_type != "command":
        raise ProtocolError(f"unsupported cloud payload type {payload_type!r}")
    command = payload.get("command")
    if not isinstance(command, dict):
        raise ProtocolError("missing cloud command")
    params = command.get("params") or {}
    if not isinstance(params, dict):
        raise ProtocolError("command params must be an object")
    expected = payload.get("expectedExtensionVersion")
    if expected is not None and not isinstance(expected, str):
        raise ProtocolError("expectedExtensionVersion must be a string")
    return CloudCommand(
        id=_required_str(command, "id"),
        profile_id=_required_str(command, "profileId"),
        action=_required_str(command, "action"),
        params=params,
        expected_extension_version=expected,
    )


def parse_hello_frame(payload: dict[str, Any]) -> HelloFrame:
    if payload.get("type") != "hello":
        raise ProtocolError("first WebSocket frame must be hello")
    profile_values = payload.get("profileIds", [])
    if not isinstance(profile_values, list):
        raise ProtocolError("profileIds must be a list when provided")
    profile_ids = tuple(
        item.strip() for item in profile_values if isinstance(item, str) and item.strip()
    )
    return HelloFrame(
        device_id=_required_str(payload, "deviceId"),
        token=_required_str(payload, "token"),
        profile_ids=profile_ids,
        client_version=_required_str(payload, "clientVersion"),
    )


def command_frame_to_json(frame: CommandFrame) -> dict[str, Any]:
    return {
        "type": "command",
        "id": frame.id,
        "profileId": frame.profile_id,
        "action": frame.action,
        "params": frame.params,
        "timeoutMs": frame.timeout_ms,
    }


def parse_result_frame(payload: dict[str, Any]) -> ResultFrame:
    if payload.get("type") != "result":
        raise ProtocolError("expected result frame")
    ok = payload.get("ok")
    if not isinstance(ok, bool):
        raise ProtocolError("result ok must be a boolean")
    error = payload.get("error")
    if error is not None and not isinstance(error, str):
        raise ProtocolError("result error must be a string")
    return ResultFrame(
        id=_required_str(payload, "id"),
        profile_id=_required_str(payload, "profileId"),
        ok=ok,
        result=payload.get("result"),
        error=error,
    )
