from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from shared.errors import (
    DeliveryError,
    binding_not_found,
    bridge_not_connected,
    profile_mismatch,
)


@dataclass(frozen=True)
class TransitCommand:
    id: str
    profile_id: str
    cloud_bridge_url: str
    action: str
    params: dict[str, Any]
    timeout_ms: int


@dataclass
class ConnectionEntry:
    device_id: str
    profile_ids: tuple[str, ...]
    ws: Any
    token: str | None = None
    last_heartbeat_at: float | None = None
    extension_connected: bool = False
    extension_version: str | None = None


@dataclass
class CommandStats:
    total: int = 0
    success: int = 0
    failure: int = 0


class RouterRegistry:
    def __init__(self, bindings: dict[str, str], binding_policies: dict[str, dict[str, Any]] | None = None):
        self._bindings = dict(bindings)
        self._binding_policies = {
            profile_id: dict(policy)
            for profile_id, policy in (binding_policies or {}).items()
            if isinstance(policy, dict)
        }
        self._connections: dict[str, ConnectionEntry] = {}
        self._inflight: dict[str, TransitCommand] = {}
        self._command_stats: dict[str, CommandStats] = {}
        self._last_cloud_result_error: str | None = None

    def update_bindings(
        self,
        bindings: dict[str, str],
        binding_policies: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._bindings = dict(bindings)
        if binding_policies is not None:
            self._binding_policies = {
                profile_id: dict(policy)
                for profile_id, policy in binding_policies.items()
                if isinstance(policy, dict)
            }

    def update_bindings_for_reconcile(
        self,
        bindings: dict[str, str],
        binding_policies: dict[str, dict[str, Any]] | None = None,
    ) -> list[TransitCommand]:
        previous_bindings = self._bindings
        self.update_bindings(bindings, binding_policies)
        failed: list[TransitCommand] = []
        for command_id, command in list(self._inflight.items()):
            if previous_bindings.get(command.profile_id) != self._bindings.get(command.profile_id):
                failed.append(command)
                self._inflight.pop(command_id, None)
        return failed

    def register(
        self,
        device_id: str,
        profile_ids: tuple[str, ...],
        ws: Any,
        token: str | None = None,
    ) -> None:
        self._connections[device_id] = ConnectionEntry(
            device_id=device_id,
            profile_ids=profile_ids,
            ws=ws,
            token=token,
        )

    def active_connections(self) -> list[ConnectionEntry]:
        return list(self._connections.values())

    def _profile_ids_for_device(self, device_id: str) -> list[str]:
        return sorted(
            profile_id
            for profile_id, bound_device_id in self._bindings.items()
            if bound_device_id == device_id
        )

    def profile_ids_for_device(self, device_id: str) -> list[str]:
        return self._profile_ids_for_device(device_id)

    def record_heartbeat(
        self,
        device_id: str,
        *,
        extension_connected: bool,
        extension_version: str | None,
    ) -> None:
        connection = self._connections.get(device_id)
        if connection is None:
            return
        connection.last_heartbeat_at = time.time()
        connection.extension_connected = extension_connected
        connection.extension_version = extension_version

    def unregister(self, device_id: str) -> list[TransitCommand]:
        self._connections.pop(device_id, None)
        failed: list[TransitCommand] = []
        for command_id, command in list(self._inflight.items()):
            if self._bindings.get(command.profile_id) == device_id:
                failed.append(command)
                self._inflight.pop(command_id, None)
        return failed

    def connection_for_profile(self, profile_id: str) -> ConnectionEntry:
        device_id = self._bindings.get(profile_id)
        if not device_id:
            raise DeliveryError(binding_not_found(profile_id))
        connection = self._connections.get(device_id)
        if connection is None:
            raise DeliveryError(bridge_not_connected(profile_id))
        return connection

    def add_inflight(self, command: TransitCommand) -> None:
        self._inflight[command.id] = command

    def get_inflight(self, command_id: str) -> TransitCommand | None:
        return self._inflight.get(command_id)

    def remove_inflight(self, command_id: str) -> TransitCommand | None:
        return self._inflight.pop(command_id, None)

    def pop_inflight(self, command_id: str, profile_id: str) -> TransitCommand:
        command = self._inflight.get(command_id)
        if command is None:
            raise DeliveryError(f"unknown command id {command_id}")
        if command.profile_id != profile_id:
            raise DeliveryError(profile_mismatch(command_id, command.profile_id, profile_id))
        return self._inflight.pop(command_id)

    def record_command_result(self, profile_id: str, *, ok: bool) -> None:
        stats = self._command_stats.setdefault(profile_id, CommandStats())
        stats.total += 1
        if ok:
            stats.success += 1
        else:
            stats.failure += 1

    def command_stats(self, profile_id: str) -> dict[str, Any]:
        stats = self._command_stats.get(profile_id) or CommandStats()
        error_rate = stats.failure / stats.total if stats.total else 0
        return {
            "total": stats.total,
            "success": stats.success,
            "failure": stats.failure,
            "errorRate": error_rate,
        }

    def all_command_stats(self) -> dict[str, dict[str, Any]]:
        profile_ids = set(self._bindings) | set(self._command_stats)
        return {
            profile_id: self.command_stats(profile_id)
            for profile_id in sorted(profile_ids)
        }

    def status(self) -> dict[str, Any]:
        command_stats = self.all_command_stats()
        return {
            "connections": sorted(self._connections),
            "connectionDetails": {
                device_id: {
                    "deviceId": connection.device_id,
                    "profileIds": self._profile_ids_for_device(connection.device_id),
                    "lastHeartbeatAt": connection.last_heartbeat_at,
                    "extensionConnected": connection.extension_connected,
                    "extensionVersion": connection.extension_version,
                    "commandStats": {
                        profile_id: command_stats.get(profile_id, self.command_stats(profile_id))
                        for profile_id in self._profile_ids_for_device(connection.device_id)
                    },
                }
                for device_id, connection in sorted(self._connections.items())
            },
            "bindings": dict(self._bindings),
            "actionPolicies": dict(self._binding_policies),
            "inFlight": sorted(self._inflight),
            "commandStats": command_stats,
            "lastCloudResultError": self._last_cloud_result_error,
        }

    def profile_status(self, profile_id: str) -> dict[str, Any]:
        device_id = self._bindings.get(profile_id)
        action_policy = dict(self._binding_policies.get(profile_id) or {})
        connection = self._connections.get(device_id or "")
        online = connection is not None
        extension_connected = bool(connection.extension_connected) if online else False
        extension_version = connection.extension_version if online else None
        last_heartbeat_at = connection.last_heartbeat_at if online else None
        items: list[dict[str, Any]] = []

        if not device_id:
            items.append(
                {
                    "key": "binding",
                    "status": "fail",
                    "title": "Browser binding is not configured",
                    "detail": f"profile {profile_id} has no browser binding",
                }
            )
        else:
            items.append(
                {
                    "key": "binding",
                    "status": "pass",
                    "title": "Browser binding is configured",
                    "detail": f"profile {profile_id} is bound to device {device_id}",
                }
            )

        if device_id:
            if online:
                items.append(
                    {
                        "key": "client",
                        "status": "pass",
                        "title": "Hermes Local Client is connected",
                        "detail": f"device {device_id} is online",
                    }
                )
            else:
                items.append(
                    {
                        "key": "client",
                        "status": "fail",
                        "title": "Hermes Local Client is not connected",
                        "detail": f"device {device_id} is offline",
                    }
                )

        if online:
            items.append(
                {
                    "key": "extension",
                    "status": "pass" if extension_connected else "warn",
                    "title": (
                        "Chrome extension is connected"
                        if extension_connected
                        else "Chrome extension is not connected"
                    ),
                    "detail": (
                        f"version {extension_version}"
                        if extension_version
                        else "Open Chrome and reload the Hermes Chrome extension"
                    ),
                }
            )

        items.append(
            {
                "key": "cloud_bridge",
                "status": "fail" if self._last_cloud_result_error else "pass",
                "title": (
                    "Cloud Chrome bridge result channel has errors"
                    if self._last_cloud_result_error
                    else "Cloud Chrome bridge result channel is healthy"
                ),
                **({"detail": self._last_cloud_result_error} if self._last_cloud_result_error else {}),
            }
        )
        tool_gate = _tool_gate_status(
            has_binding=bool(device_id),
            online=online,
            action_policy=action_policy,
        )

        return {
            "profileId": profile_id,
            "deviceId": device_id,
            "online": online,
            "extensionConnected": extension_connected,
            "extensionVersion": extension_version,
            "lastHeartbeatAt": last_heartbeat_at,
            "lastCloudResultError": self._last_cloud_result_error,
            "actionPolicy": action_policy,
            "commandStats": self.command_stats(profile_id),
            "toolGate": tool_gate,
            "items": items,
        }

    def record_cloud_result_error(self, error: str) -> None:
        self._last_cloud_result_error = error

    def clear_cloud_result_error(self) -> None:
        self._last_cloud_result_error = None


def _tool_gate_status(
    *,
    has_binding: bool,
    online: bool,
    action_policy: dict[str, Any],
) -> dict[str, Any]:
    mode = str(
        action_policy.get("local_action_mode")
        or action_policy.get("localActionMode")
        or ""
    ).strip().lower()
    if mode == "deny":
        return {
            "enabled": False,
            "reason": "profile action policy denies Chrome control",
        }
    if not has_binding:
        return {
            "enabled": False,
            "reason": "profile browser binding is not configured",
        }
    if not online:
        return {
            "enabled": False,
            "reason": "Hermes Local Client is not connected for this profile",
        }
    return {
        "enabled": True,
        "reason": "profile browser binding and Local Client are ready",
    }
