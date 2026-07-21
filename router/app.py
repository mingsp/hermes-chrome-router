from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import WSMsgType, web

from router.audit import CommandAuditStore
from router.bindings import WebUIBindingProvider
from router.cloud_bridge import CloudBridgeClient, MalformedCloudCommand
from router.config import RouterConfig
from router.device_tokens import WebUIDeviceTokenVerifier
from router.registry import RouterRegistry, TransitCommand
from shared.errors import DeliveryError, ProtocolError
from shared.errors import profile_mismatch as profile_mismatch_message
from shared.protocol import (
    CommandFrame,
    command_frame_to_json,
    parse_hello_frame,
    parse_result_frame,
)
from shared.timing import ROUTER_DELIVERY_TIMEOUT_MS


logger = logging.getLogger(__name__)
_URL_PATTERN = re.compile(r"https?://[^\s]+")
POLL_LOOP_ERROR_LOG_INTERVAL_SECONDS = 30.0


class _RepeatedErrorLogLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.last_error: str | None = None
        self.last_logged_at = 0.0
        self.suppressed_count = 0

    def should_log(self, error: str, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        if error != self.last_error:
            self.last_error = error
            self.last_logged_at = now
            self.suppressed_count = 0
            return True, 0

        if now - self.last_logged_at >= self.interval_seconds:
            suppressed_count = self.suppressed_count
            self.last_logged_at = now
            self.suppressed_count = 0
            return True, suppressed_count

        self.suppressed_count += 1
        return False, self.suppressed_count

    def clear(self) -> int:
        suppressed_count = self.suppressed_count
        self.last_error = None
        self.last_logged_at = 0.0
        self.suppressed_count = 0
        return suppressed_count


def _make_transit_factory(config: RouterConfig):
    def factory(
        command_id: str,
        profile_id: str,
        action: str,
        params: dict[str, Any],
        timeout_ms: int,
    ) -> TransitCommand:
        return TransitCommand(
            id=command_id,
            profile_id=profile_id,
            cloud_bridge_url=config.cloud_bridge_url,
            action=action,
            params=params,
            timeout_ms=timeout_ms,
        )

    return factory


def _sanitize_url_for_log(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parsed.scheme or not parsed.hostname:
        if parsed.scheme and parsed.netloc:
            return urlunsplit((parsed.scheme, "<invalid-url>", parsed.path, "", ""))
        return value.split("?", 1)[0].split("#", 1)[0]
    netloc = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _sanitize_error_message(exc: Exception) -> str:
    return _URL_PATTERN.sub(
        lambda match: _sanitize_url_for_log(match.group(0)),
        str(exc),
    )


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _status(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        return web.json_response({"error": "unauthorized"}, status=401)
    registry: RouterRegistry = request.app["registry"]
    payload = registry.status()
    return web.json_response(payload)


async def _metrics(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        return web.json_response({"error": "unauthorized"}, status=401)
    registry: RouterRegistry = request.app["registry"]
    status = registry.status()
    queued_commands = sum(len(queue) for queue in request.app["queues"].values())
    lines = [
        "# HELP hermes_chrome_router_connected_clients Number of connected Hermes Local Client websocket sessions.",
        "# TYPE hermes_chrome_router_connected_clients gauge",
        f"hermes_chrome_router_connected_clients {len(status['connections'])}",
        "# HELP hermes_chrome_router_bound_profiles Number of profile browser bindings known by the router.",
        "# TYPE hermes_chrome_router_bound_profiles gauge",
        f"hermes_chrome_router_bound_profiles {len(status['bindings'])}",
        "# HELP hermes_chrome_router_inflight_commands Number of commands delivered to Local Clients and waiting for results.",
        "# TYPE hermes_chrome_router_inflight_commands gauge",
        f"hermes_chrome_router_inflight_commands {len(status['inFlight'])}",
        "# HELP hermes_chrome_router_queued_commands Number of commands queued before Local Client delivery.",
        "# TYPE hermes_chrome_router_queued_commands gauge",
        f"hermes_chrome_router_queued_commands {queued_commands}",
        "# HELP hermes_chrome_router_cloud_result_error Whether the cloud bridge result channel currently has an error.",
        "# TYPE hermes_chrome_router_cloud_result_error gauge",
        f"hermes_chrome_router_cloud_result_error {1 if status['lastCloudResultError'] else 0}",
        "# HELP hermes_chrome_router_profile_online Whether each bound profile has an online Local Client connection.",
        "# TYPE hermes_chrome_router_profile_online gauge",
    ]
    connection_details = status["connectionDetails"]
    for profile_id, device_id in sorted(status["bindings"].items()):
        connection = connection_details.get(device_id) or {}
        online = profile_id in (connection.get("profileIds") or [])
        labels = _prometheus_labels(profile_id=profile_id, device_id=device_id)
        lines.append(f"hermes_chrome_router_profile_online{{{labels}}} {1 if online else 0}")
    lines.extend(
        [
            "# HELP hermes_chrome_router_profile_commands_total Total Chrome commands completed by profile.",
            "# TYPE hermes_chrome_router_profile_commands_total counter",
            "# HELP hermes_chrome_router_profile_command_errors_total Total failed Chrome commands by profile.",
            "# TYPE hermes_chrome_router_profile_command_errors_total counter",
            "# HELP hermes_chrome_router_profile_command_error_rate Failed Chrome commands divided by completed commands by profile.",
            "# TYPE hermes_chrome_router_profile_command_error_rate gauge",
        ]
    )
    for profile_id, stats in sorted(status["commandStats"].items()):
        labels = _prometheus_labels(profile_id=profile_id)
        lines.append(f"hermes_chrome_router_profile_commands_total{{{labels}}} {stats['total']}")
        lines.append(f"hermes_chrome_router_profile_command_errors_total{{{labels}}} {stats['failure']}")
        lines.append(f"hermes_chrome_router_profile_command_error_rate{{{labels}}} {stats['errorRate']}")
    body = "\n".join(lines) + "\n"
    return web.Response(
        body=body.encode("utf-8"),
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
    )


def _prometheus_labels(**labels: str) -> str:
    return ",".join(
        f'{key}="{_prometheus_escape(str(value))}"'
        for key, value in labels.items()
    )


def _prometheus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


async def _profile_status(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        return web.json_response({"error": "unauthorized"}, status=401)
    registry: RouterRegistry = request.app["registry"]
    profile_id = str(request.match_info.get("profile_id") or "").strip()
    if not profile_id:
        raise web.HTTPBadRequest(text="profile_id is required")
    return web.json_response(registry.profile_status(profile_id))


async def _audit_commands(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        return web.json_response({"error": "unauthorized"}, status=401)
    audit_store: CommandAuditStore | None = request.app.get("audit_store")
    if audit_store is None:
        return web.json_response(
            {
                "count": 0,
                "total": 0,
                "limit": _query_int(request, "limit", 100),
                "offset": _query_int(request, "offset", 0),
                "items": [],
            }
        )
    limit = _query_int(request, "limit", 100)
    offset = _query_int(request, "offset", 0)
    return web.json_response(
        audit_store.search(
            profile_id=str(request.query.get("profileId") or request.query.get("profile_id") or "").strip(),
            command_id=str(request.query.get("commandId") or request.query.get("command_id") or "").strip(),
            device_id=str(request.query.get("deviceId") or request.query.get("device_id") or "").strip(),
            action=str(request.query.get("action") or "").strip(),
            status=str(request.query.get("status") or "").strip(),
            q=str(request.query.get("q") or "").strip(),
            from_ms=_query_optional_int(request, "fromMs", "from_ms"),
            to_ms=_query_optional_int(request, "toMs", "to_ms"),
            limit=limit,
            offset=offset,
        )
    )


async def _audit_summary(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        return web.json_response({"error": "unauthorized"}, status=401)
    audit_store: CommandAuditStore | None = request.app.get("audit_store")
    if audit_store is None:
        return web.json_response(_empty_audit_summary())
    return web.json_response(
        audit_store.summary(
            profile_id=str(request.query.get("profileId") or request.query.get("profile_id") or "").strip(),
            command_id=str(request.query.get("commandId") or request.query.get("command_id") or "").strip(),
            device_id=str(request.query.get("deviceId") or request.query.get("device_id") or "").strip(),
            action=str(request.query.get("action") or "").strip(),
            status=str(request.query.get("status") or "").strip(),
            q=str(request.query.get("q") or "").strip(),
            from_ms=_query_optional_int(request, "fromMs", "from_ms"),
            to_ms=_query_optional_int(request, "toMs", "to_ms"),
        )
    )


def _empty_audit_summary() -> dict[str, Any]:
    return {
        "total": 0,
        "failed": 0,
        "profiles": 0,
        "devices": 0,
        "byProfile": [],
        "byStatus": [],
        "byAction": [],
    }


async def _management_overview(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        return web.json_response({"error": "unauthorized"}, status=401)

    registry: RouterRegistry = request.app["registry"]
    status = registry.status()
    last_cloud_error = status.get("lastCloudResultError")
    audit_store: CommandAuditStore | None = request.app.get("audit_store")
    audit_summary = audit_store.summary() if audit_store is not None else _empty_audit_summary()
    connection_details = status.get("connectionDetails", {})
    if not isinstance(connection_details, dict):
        connection_details = {}
    bindings = status.get("bindings", {})
    if not isinstance(bindings, dict):
        bindings = {}

    devices = [
        {
            "deviceId": details.get("deviceId", device_id),
            "profileIds": details.get("profileIds", []),
            "lastHeartbeatAt": details.get("lastHeartbeatAt"),
            "extensionConnected": bool(details.get("extensionConnected")),
            "extensionVersion": details.get("extensionVersion"),
            "commandStats": details.get("commandStats", {}),
        }
        for device_id, details in sorted(connection_details.items())
    ]

    binding_rows = []
    for profile_id, device_id in sorted(bindings.items()):
        profile = registry.profile_status(profile_id)
        binding_rows.append(
            {
                "profileId": profile.get("profileId", profile_id),
                "deviceId": profile.get("deviceId") or device_id,
                "online": bool(profile.get("online")),
                "extensionConnected": bool(profile.get("extensionConnected")),
                "actionPolicy": profile.get("actionPolicy", {}),
                "toolGate": profile.get("toolGate", {}),
                "commandStats": profile.get("commandStats", {}),
            }
        )

    return web.json_response(
        {
            "router": {
                "status": "degraded" if last_cloud_error else "healthy",
                "now": int(time.time()),
                "minClientVersion": config.min_client_version,
                "rateLimitPerProfile": config.rate_limit_per_profile,
                "rateLimitBurst": config.rate_limit_burst,
                "auditEnabled": audit_store is not None,
            },
            "cloudBridge": {
                "url": config.cloud_bridge_url,
                "healthy": not bool(last_cloud_error),
                "lastError": last_cloud_error,
            },
            "connections": {
                "total": len(devices),
                "devices": devices,
            },
            "bindings": binding_rows,
            "commands": {
                "inFlight": status.get("inFlight", []),
                "statsByProfile": status.get("commandStats", {}),
                "auditSummary": audit_summary,
            },
        }
    )


def _query_optional_int(request: web.Request, *keys: str) -> int | None:
    for key in keys:
        raw = request.query.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(str(raw))
        except ValueError:
            return None
    return None


def _query_int(request: web.Request, key: str, default: int) -> int:
    try:
        return int(str(request.query.get(key) or default))
    except ValueError:
        return default


async def _refresh_bindings(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        logger.info("binding_refresh_unauthorized source=endpoint")
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        bindings, policies = await _load_binding_snapshot(request.app)
    except Exception as exc:
        sanitized_error = _sanitize_error_message(exc)
        request.app["registry"].record_cloud_result_error(sanitized_error)
        logger.warning("binding_refresh_failed source=endpoint error=%s", sanitized_error)
        return web.json_response({"error": sanitized_error}, status=502)
    request.app["registry"].clear_cloud_result_error()
    failed_commands = request.app["registry"].update_bindings_for_reconcile(bindings, policies)
    for command in failed_commands:
        await _fail_command(request.app, command, "profile unbound from device")
    await _reconcile_active_connections(request.app)
    logger.info(
        "binding_refresh_success source=endpoint binding_count=%s policy_count=%s",
        len(bindings),
        len(policies),
    )
    return web.json_response({"ok": True, "bindings": bindings, "actionPolicies": policies})


def _is_authorized_router_request(request: web.Request, config: RouterConfig) -> bool:
    if not config.router_token:
        return True
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return token == config.router_token


async def _reconcile_active_connections(app: web.Application) -> None:
    registry: RouterRegistry = app["registry"]
    for connection in registry.active_connections():
        try:
            if await _is_connection_authorized_for_reconcile(app, connection):
                await connection.ws.send_json(
                    {
                        "type": "binding_update",
                        "bindings": [
                            {"profileId": profile_id}
                            for profile_id in registry.profile_ids_for_device(connection.device_id)
                        ],
                    }
                )
                continue

            await connection.ws.send_json(
                {
                    "type": "error",
                    "error": "unauthorized",
                    "reason": "device_authorization_revoked",
                }
            )
            failed_commands = registry.unregister(connection.device_id)
            logger.info(
                "bridge_authorization_revoked device_id=%s failed_inflight_count=%s",
                connection.device_id,
                len(failed_commands),
            )
            for command in failed_commands:
                await _fail_command(app, command, "device authorization revoked")
            await connection.ws.close(code=4001, message=b"device_authorization_revoked")
        except Exception as exc:
            await _fail_reconcile_connection(app, connection, exc)


async def _fail_reconcile_connection(app: web.Application, connection, exc: Exception) -> None:
    registry: RouterRegistry = app["registry"]
    error = f"websocket reconcile failed: {_sanitize_error_message(exc)}"
    failed_commands = registry.unregister(connection.device_id)
    logger.info(
        "bridge_reconcile_failed device_id=%s failed_inflight_count=%s error=%s",
        connection.device_id,
        len(failed_commands),
        error,
    )
    for command in failed_commands:
        await _fail_command(app, command, error)


async def _is_connection_authorized_for_reconcile(app: web.Application, connection) -> bool:
    config: RouterConfig = app["config"]
    if config.router_token and connection.token == config.router_token:
        return True
    if not connection.token:
        return False
    verifier = app.get("device_token_verifier")
    if verifier is None:
        return False
    try:
        verified_device_id = await verifier.verify_device_token(connection.token)
    except Exception as exc:
        app["registry"].record_cloud_result_error(f"device token verification failed: {exc}")
        return False
    return verified_device_id == connection.device_id


async def _bridge_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    config: RouterConfig = request.app["config"]
    registry: RouterRegistry = request.app["registry"]
    cloud_bridge = request.app["cloud_bridge"]
    device_id: str | None = None

    try:
        first = await ws.receive_json(timeout=5)
        hello = parse_hello_frame(first)
        if not await _authorize_hello(request.app, hello):
            logger.info("bridge_hello_unauthorized device_id=%s", hello.device_id)
            await ws.send_json({"type": "error", "error": "unauthorized"})
            await ws.close(code=4001, message=b"unauthorized")
            return ws
        if _is_client_version_too_old(hello.client_version, config.min_client_version):
            logger.info(
                "bridge_hello_version_rejected device_id=%s client_version=%s min_client_version=%s",
                hello.device_id,
                hello.client_version,
                config.min_client_version,
            )
            await ws.send_json(
                {
                    "type": "error",
                    "error": "version_too_old",
                    "minClientVersion": config.min_client_version,
                }
            )
            await ws.close(code=4003, message=b"version_too_old")
            return ws
        device_id = hello.device_id
        registry.register(hello.device_id, hello.profile_ids, ws, token=hello.token)
        logger.info(
            "bridge_hello_authorized device_id=%s client_version=%s binding_count=%s",
            hello.device_id,
            hello.client_version,
            len(registry.profile_ids_for_device(hello.device_id)),
        )
        await ws.send_json(
            {
                "type": "hello_ack",
                "deviceId": hello.device_id,
                "minClientVersion": config.min_client_version,
                "bindings": [
                    {"profileId": profile_id}
                    for profile_id in registry.profile_ids_for_device(hello.device_id)
                ],
            }
        )

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            payload = msg.json()
            if payload.get("type") == "heartbeat":
                if device_id:
                    registry.record_heartbeat(
                        device_id,
                        extension_connected=bool(payload.get("extensionConnected")),
                        extension_version=(
                            payload.get("extensionVersion")
                            if isinstance(payload.get("extensionVersion"), str)
                            else None
                        ),
                    )
                logger.debug(
                    "bridge_heartbeat device_id=%s extension_connected=%s extension_version=%s",
                    device_id,
                    bool(payload.get("extensionConnected")),
                    (
                        payload.get("extensionVersion")
                        if isinstance(payload.get("extensionVersion"), str)
                        else None
                    ),
                )
                await ws.send_json({"type": "heartbeat_ack", "timestamp": payload.get("timestamp")})
                continue
            if payload.get("type") == "result":
                result = parse_result_frame(payload)
                logger.info(
                    "command_result_received command_id=%s profile_id=%s ok=%s",
                    result.id,
                    result.profile_id,
                    result.ok,
                )
                command = registry.get_inflight(result.id)
                if command is None:
                    await ws.send_json({"type": "error", "error": f"unknown command id {result.id}"})
                    continue
                if command.profile_id != result.profile_id:
                    await _fail_command(
                        request.app,
                        command,
                        profile_mismatch_message(result.id, command.profile_id, result.profile_id),
                    )
                    await ws.send_json(
                        {
                            "type": "error",
                            "error": profile_mismatch_message(result.id, command.profile_id, result.profile_id),
                        }
                    )
                    continue
                registry.pop_inflight(result.id, result.profile_id)
                _cancel_timeout(request.app, result.id)
                _clear_current(request.app, command)
                _record_audit(
                    request.app,
                    command,
                    "completed" if result.ok else "failed",
                    detail=result.error or "",
                )
                await _post_cloud_result(
                    request.app,
                    result.id,
                    result.ok,
                    profile_id=result.profile_id,
                    result=result.result,
                    error=result.error,
                    cloud_bridge_url=command.cloud_bridge_url,
                )
                await _dispatch_next_for_profile(request.app, result.profile_id)
                continue
            logger.debug(
                "bridge_unsupported_frame device_id=%s frame_type=%s",
                device_id,
                payload.get("type"),
            )
            await ws.send_json({"type": "error", "error": f"unsupported frame type {payload.get('type')}"})
    except (ProtocolError, DeliveryError, TimeoutError) as exc:
        logger.info("bridge_error device_id=%s error=%s", device_id, _sanitize_error_message(exc))
        await ws.send_json({"type": "error", "error": str(exc)})
    finally:
        if device_id:
            failed_commands = registry.unregister(device_id)
            logger.info(
                "bridge_disconnected device_id=%s failed_inflight_count=%s",
                device_id,
                len(failed_commands),
            )
            for command in failed_commands:
                await _fail_command(
                    request.app,
                    command,
                    f"browser bridge disconnected for profile {command.profile_id}",
                )
    return ws


async def _authorize_hello(app: web.Application, hello) -> bool:
    config: RouterConfig = app["config"]
    if config.router_token and hello.token == config.router_token:
        return True

    verifier = app.get("device_token_verifier")
    if verifier is None:
        return False
    try:
        verified_device_id = await verifier.verify_device_token(hello.token)
    except Exception as exc:
        app["registry"].record_cloud_result_error(f"device token verification failed: {exc}")
        return False
    return verified_device_id == hello.device_id


def _is_client_version_too_old(client_version: str, min_client_version: str) -> bool:
    return _version_tuple(client_version) < _version_tuple(min_client_version)


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = []
    for raw_part in version.split(".")[:3]:
        digits = ""
        for char in raw_part:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits or "0"))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


async def dispatch_command(app: web.Application, command) -> None:
    logger.info(
        "command_received command_id=%s profile_id=%s action=%s",
        command.id,
        command.profile_id,
        command.action,
    )
    if _is_rate_limited(app, command.profile_id):
        logger.info(
            "command_rate_limited command_id=%s profile_id=%s action=%s",
            command.id,
            command.profile_id,
            command.action,
        )
        await _post_cloud_result(
            app,
            command.id,
            False,
            profile_id=command.profile_id,
            error=f"rate limit exceeded for profile {command.profile_id}",
        )
        return
    transit = TransitCommand(
        id=command.id,
        profile_id=command.profile_id,
        cloud_bridge_url=app["config"].cloud_bridge_url,
        action=command.action,
        params=command.params,
        timeout_ms=ROUTER_DELIVERY_TIMEOUT_MS,
    )
    await _enqueue_local_command(app, transit)


async def _enqueue_local_command(app: web.Application, command: TransitCommand) -> None:
    registry: RouterRegistry = app["registry"]
    try:
        connection = registry.connection_for_profile(command.profile_id)
    except DeliveryError as exc:
        sanitized_error = _sanitize_error_message(exc)
        logger.info(
            "command_delivery_failed command_id=%s profile_id=%s action=%s error=%s",
            command.id,
            command.profile_id,
            command.action,
            sanitized_error,
        )
        _record_audit(app, command, "failed", device_id=_bound_device_id(app, command.profile_id), detail=str(exc))
        await _post_cloud_result(
            app,
            command.id,
            False,
            profile_id=command.profile_id,
            error=str(exc),
            cloud_bridge_url=command.cloud_bridge_url,
        )
        return
    _record_audit(app, command, "queued", device_id=connection.device_id)
    app["queues"][command.profile_id].append(command)
    logger.info(
        "command_queued command_id=%s profile_id=%s action=%s device_id=%s queue_depth=%s",
        command.id,
        command.profile_id,
        command.action,
        connection.device_id,
        len(app["queues"][command.profile_id]),
    )
    await _dispatch_next_for_profile(app, command.profile_id)


def _is_rate_limited(app: web.Application, profile_id: str) -> bool:
    config: RouterConfig = app["config"]
    if config.rate_limit_per_profile <= 0 or config.rate_limit_burst <= 0:
        return False
    now = time.monotonic()
    window = 1.0
    entries = app["rate_limit_windows"][profile_id]
    while entries and now - entries[0] >= window:
        entries.popleft()
    allowed = min(config.rate_limit_per_profile, config.rate_limit_burst)
    if len(entries) >= allowed:
        return True
    entries.append(now)
    return False


async def _dispatch_next_for_profile(app: web.Application, profile_id: str) -> None:
    current_by_profile: dict[str, str] = app["current_by_profile"]
    if profile_id in current_by_profile:
        return
    queue = app["queues"][profile_id]
    if not queue:
        return
    registry: RouterRegistry = app["registry"]
    command = queue.popleft()
    try:
        connection = registry.connection_for_profile(command.profile_id)
        registry.add_inflight(command)
        current_by_profile[command.profile_id] = command.id
        try:
            _record_audit(app, command, "delivered", device_id=connection.device_id)
            logger.info(
                "command_delivered command_id=%s profile_id=%s action=%s device_id=%s",
                command.id,
                command.profile_id,
                command.action,
                connection.device_id,
            )
            await connection.ws.send_json(
                command_frame_to_json(
                    CommandFrame(
                        id=command.id,
                        profile_id=command.profile_id,
                        action=command.action,
                        params=command.params,
                        timeout_ms=command.timeout_ms,
                    )
                )
            )
        except Exception as exc:
            sanitized_error = _sanitize_error_message(exc)
            logger.info(
                "command_delivery_failed command_id=%s profile_id=%s action=%s device_id=%s error=%s",
                command.id,
                command.profile_id,
                command.action,
                connection.device_id,
                sanitized_error,
            )
            registry.remove_inflight(command.id)
            _clear_current(app, command)
            _record_audit(app, command, "failed", device_id=connection.device_id, detail=str(exc))
            await _post_cloud_result(
                app,
                command.id,
                False,
                profile_id=command.profile_id,
                error=str(exc),
                cloud_bridge_url=command.cloud_bridge_url,
            )
            await _dispatch_next_for_profile(app, profile_id)
            return
        app["timeout_tasks"][command.id] = asyncio.create_task(_timeout_command(app, command))
    except DeliveryError as exc:
        sanitized_error = _sanitize_error_message(exc)
        logger.info(
            "command_delivery_failed command_id=%s profile_id=%s action=%s error=%s",
            command.id,
            command.profile_id,
            command.action,
            sanitized_error,
        )
        _record_audit(app, command, "failed", detail=str(exc))
        await _post_cloud_result(
            app,
            command.id,
            False,
            profile_id=command.profile_id,
            error=str(exc),
            cloud_bridge_url=command.cloud_bridge_url,
        )
        await _dispatch_next_for_profile(app, profile_id)


async def _timeout_command(app: web.Application, command: TransitCommand) -> None:
    await asyncio.sleep(command.timeout_ms / 1000)
    if app["registry"].get_inflight(command.id) is None:
        return
    logger.info(
        "command_timeout command_id=%s profile_id=%s action=%s timeout_ms=%s",
        command.id,
        command.profile_id,
        command.action,
        command.timeout_ms,
    )
    await _fail_command(
        app,
        command,
        f"browser bridge timed out for profile {command.profile_id}",
    )


async def _fail_command(app: web.Application, command: TransitCommand, error: str) -> None:
    registry: RouterRegistry = app["registry"]
    registry.remove_inflight(command.id)
    _cancel_timeout(app, command.id)
    _remove_from_queue(app, command)
    _clear_current(app, command)
    _record_audit(app, command, "failed", detail=error)
    logger.info(
        "command_failed command_id=%s profile_id=%s action=%s error=%s",
        command.id,
        command.profile_id,
        command.action,
        _sanitize_error_message(Exception(error)),
    )
    await _post_cloud_result(
        app,
        command.id,
        False,
        profile_id=command.profile_id,
        error=error,
        cloud_bridge_url=command.cloud_bridge_url,
    )
    await _dispatch_next_for_profile(app, command.profile_id)


async def _post_cloud_result(
    app: web.Application,
    command_id: str,
    ok: bool,
    *,
    profile_id: str | None = None,
    result=None,
    error: str | None = None,
    cloud_bridge_url: str | None = None,
) -> None:
    registry: RouterRegistry = app["registry"]
    if profile_id:
        registry.record_command_result(profile_id, ok=ok)
        command = registry.get_inflight(command_id)
        if command is not None:
            _record_audit(
                app,
                command,
                "completed" if ok else "failed",
                detail=error or "",
            )
    try:
        cloud_bridge = _cloud_bridge_for_result(app, cloud_bridge_url)
        await cloud_bridge.post_result(command_id, ok, result=result, error=error)
        registry.clear_cloud_result_error()
        logger.info(
            "cloud_result_posted command_id=%s profile_id=%s ok=%s",
            command_id,
            profile_id,
            ok,
        )
    except Exception as exc:
        sanitized_error = _sanitize_error_message(exc)
        registry.record_cloud_result_error(str(exc))
        logger.warning(
            "cloud_result_post_failed command_id=%s profile_id=%s ok=%s error=%s",
            command_id,
            profile_id,
            ok,
            sanitized_error,
        )


def _cloud_bridge_for_result(app: web.Application, cloud_bridge_url: str | None):
    if not cloud_bridge_url:
        return app["cloud_bridge"]
    normalized_url = cloud_bridge_url.rstrip("/")
    config: RouterConfig = app["config"]
    if normalized_url == config.cloud_bridge_url.rstrip("/"):
        return app["cloud_bridge"]
    return CloudBridgeClient(normalized_url, token=config.router_token)


def _cancel_timeout(app: web.Application, command_id: str) -> None:
    task = app["timeout_tasks"].pop(command_id, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()


def _clear_current(app: web.Application, command: TransitCommand) -> None:
    if app["current_by_profile"].get(command.profile_id) == command.id:
        app["current_by_profile"].pop(command.profile_id, None)


def _remove_from_queue(app: web.Application, command: TransitCommand) -> None:
    queue = app["queues"].get(command.profile_id)
    if not queue:
        return
    app["queues"][command.profile_id] = deque(
        item for item in queue if item.id != command.id
    )


def _record_audit(
    app: web.Application,
    command: TransitCommand,
    status: str,
    *,
    device_id: str | None = None,
    detail: str = "",
) -> None:
    audit_store: CommandAuditStore | None = app.get("audit_store")
    if audit_store is None:
        return
    audit_store.record(
        command_id=command.id,
        profile_id=command.profile_id,
        action=command.action,
        status=status,
        params=command.params,
        device_id=device_id,
        detail=detail,
    )


def _bound_device_id(app: web.Application, profile_id: str) -> str | None:
    registry: RouterRegistry = app["registry"]
    bindings = registry.status().get("bindings", {})
    if isinstance(bindings, dict):
        device_id = bindings.get(profile_id)
        return str(device_id) if device_id else None
    return None


async def _poll_loop(app: web.Application) -> None:
    router_id = uuid.uuid4().hex[:8]
    cloud_bridge = app["cloud_bridge"]
    empty_polls = 0
    error_log_limiter = _RepeatedErrorLogLimiter(
        float(
            app.get(
                "poll_loop_error_log_interval_seconds",
                POLL_LOOP_ERROR_LOG_INTERVAL_SECONDS,
            )
        )
    )
    logger.info("poll_loop_start router_id=%s", router_id)
    while True:
        try:
            command = await cloud_bridge.poll_next(router_id)
            suppressed_errors = error_log_limiter.clear()
            if suppressed_errors:
                logger.info(
                    "poll_loop_recovered router_id=%s suppressed_errors=%s",
                    router_id,
                    suppressed_errors,
                )
            if isinstance(command, MalformedCloudCommand):
                sanitized_error = _sanitize_error_message(Exception(command.error))
                logger.info(
                    "cloud_command_malformed command_id=%s error=%s",
                    command.id,
                    sanitized_error,
                )
                await _post_cloud_result(app, command.id, False, error=command.error)
            elif command is not None:
                empty_polls = 0
                await dispatch_command(app, command)
            else:
                empty_polls += 1
                if empty_polls % 1200 == 0:
                    logger.debug(
                        "poll_loop_idle router_id=%s empty_polls=%s",
                        router_id,
                        empty_polls,
                    )
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            logger.info("poll_loop_cancelled router_id=%s", router_id)
            raise
        except Exception as exc:
            sanitized_error = _sanitize_error_message(exc)
            app["registry"].record_cloud_result_error(str(exc))
            should_log, suppressed_errors = error_log_limiter.should_log(sanitized_error)
            if should_log:
                logger.warning(
                    "poll_loop_error router_id=%s error=%s suppressed_errors=%s",
                    router_id,
                    sanitized_error,
                    suppressed_errors,
                )
            await asyncio.sleep(0.2)


async def _load_binding_snapshot(app: web.Application) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    binding_provider = app.get("binding_provider")
    if binding_provider is None:
        registry_status = app["registry"].status()
        return dict(registry_status["bindings"]), dict(registry_status["actionPolicies"])
    if hasattr(binding_provider, "load_binding_snapshot"):
        bindings, policies = await binding_provider.load_binding_snapshot()
        return dict(bindings), dict(policies)
    return dict(await binding_provider.load_bindings()), {}


async def _on_startup(app: web.Application) -> None:
    config: RouterConfig = app["config"]
    logger.info(
        "router_start host=%s port=%s cloud_bridge_url=%s web_ui_bindings=%s binding_count=%s audit_enabled=%s min_client_version=%s",
        config.host,
        config.port,
        _sanitize_url_for_log(config.cloud_bridge_url),
        bool(app.get("binding_provider")),
        len(config.bindings),
        bool(app.get("audit_store")),
        config.min_client_version,
    )
    binding_provider = app.get("binding_provider")
    if binding_provider is not None:
        try:
            bindings, policies = await _load_binding_snapshot(app)
            app["registry"].update_bindings(bindings, policies)
            app["registry"].clear_cloud_result_error()
            logger.info(
                "binding_refresh_success source=startup binding_count=%s policy_count=%s",
                len(bindings),
                len(policies),
            )
        except Exception as exc:
            sanitized_error = _sanitize_error_message(exc)
            app["registry"].record_cloud_result_error(sanitized_error)
            logger.warning("binding_refresh_failed source=startup error=%s", sanitized_error)
    if app["start_poller"]:
        app["poll_task"] = asyncio.create_task(_poll_loop(app))


async def _on_cleanup(app: web.Application) -> None:
    task = app.get("poll_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    for timeout_task in list(app["timeout_tasks"].values()):
        timeout_task.cancel()
    if app["timeout_tasks"]:
        await asyncio.gather(*app["timeout_tasks"].values(), return_exceptions=True)
        app["timeout_tasks"].clear()
    audit_store = app.get("audit_store")
    if audit_store is not None and hasattr(audit_store, "close"):
        audit_store.close()


def create_router_app(
    config: RouterConfig,
    cloud_bridge=None,
    binding_provider=None,
    device_token_verifier=None,
    *,
    start_poller: bool = True,
) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["registry"] = RouterRegistry(config.bindings, config.binding_policies)
    app["cloud_bridge"] = cloud_bridge or CloudBridgeClient(
        config.cloud_bridge_url,
        token=config.router_token,
    )
    app["binding_provider"] = binding_provider or (
        WebUIBindingProvider(config.web_ui_url, token=config.router_token)
        if config.web_ui_url
        else None
    )
    app["device_token_verifier"] = device_token_verifier or (
        WebUIDeviceTokenVerifier(config.web_ui_url)
        if config.web_ui_url
        else None
    )
    app["audit_store"] = CommandAuditStore(config.audit_db_path) if config.audit_db_path else None
    app["start_poller"] = start_poller
    app["queues"] = defaultdict(deque)
    app["current_by_profile"] = {}
    app["timeout_tasks"] = {}
    app["rate_limit_windows"] = defaultdict(deque)
    app["transit_command_factory"] = _make_transit_factory(config)
    app.router.add_get("/health", _health)
    app.router.add_get("/status", _status)
    app.router.add_get("/metrics", _metrics)
    app.router.add_get("/audit/commands", _audit_commands)
    app.router.add_get("/audit/summary", _audit_summary)
    app.router.add_get("/management/overview", _management_overview)
    app.router.add_post("/bindings/refresh", _refresh_bindings)
    app.router.add_get("/status/profile/{profile_id}", _profile_status)
    app.router.add_get("/bridge", _bridge_ws)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app
