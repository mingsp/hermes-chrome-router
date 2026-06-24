from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse


Check = dict[str, str]


def _check(check_id: str, label: str, status: str, detail: str) -> Check:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _env_value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "") or "").strip()


def _url_scheme(value: str) -> str:
    return urlparse(value).scheme.lower()


def _peer_router_check(env: Mapping[str, str]) -> Check:
    raw = _env_value(env, "HERMES_CHROME_ROUTER_PEERS")
    server_id = _env_value(env, "HERMES_CHROME_ROUTER_SERVER_ID") or "local"
    if not raw:
        return _check(
            "peer-routers",
            "Peer Router map",
            "fail",
            "HERMES_CHROME_ROUTER_PEERS is required for production failover.",
        )
    try:
        peers = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _check("peer-routers", "Peer Router map", "fail", f"invalid JSON: {exc.msg}")
    if not isinstance(peers, dict) or not peers:
        return _check("peer-routers", "Peer Router map", "fail", "peer map must be a non-empty JSON object.")
    if server_id in peers:
        return _check("peer-routers", "Peer Router map", "fail", "peer map must not include this Router serverId.")
    invalid: list[str] = []
    for peer_id, peer_url in peers.items():
        peer_id = str(peer_id or "").strip()
        peer_url = str(peer_url or "").strip()
        if not peer_id or _url_scheme(peer_url) not in {"http", "https"}:
            invalid.append(peer_id or "<empty>")
    if invalid:
        return _check(
            "peer-routers",
            "Peer Router map",
            "fail",
            f"peer URLs must be internal http(s) base URLs: {', '.join(invalid)}",
        )
    return _check("peer-routers", "Peer Router map", "pass", f"{len(peers)} peer Router(s) configured.")


def gateway_readiness_report(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = env if env is not None else os.environ
    checks: list[Check] = []

    router_token = _env_value(values, "HERMES_CHROME_ROUTER_TOKEN")
    if not router_token:
        checks.append(_check("router-token", "Router token", "fail", "HERMES_CHROME_ROUTER_TOKEN is required."))
    elif router_token == "dev-token":
        checks.append(_check("router-token", "Router token", "fail", "dev fallback token must not be used in production."))
    else:
        checks.append(_check("router-token", "Router token", "pass", "Router token is configured."))

    cloud_bridge_url = _env_value(values, "HERMES_CHROME_CLOUD_BRIDGE_URL")
    if not cloud_bridge_url:
        checks.append(
            _check("cloud-bridge-url", "Cloud bridge URL", "fail", "HERMES_CHROME_CLOUD_BRIDGE_URL is required.")
        )
    elif _url_scheme(cloud_bridge_url) not in {"http", "https"}:
        checks.append(_check("cloud-bridge-url", "Cloud bridge URL", "fail", "cloud bridge URL must be http(s)."))
    elif "127.0.0.1" in cloud_bridge_url or "localhost" in cloud_bridge_url:
        checks.append(
            _check(
                "cloud-bridge-url",
                "Cloud bridge URL",
                "warn",
                "loopback cloud bridge URL is suitable for development only.",
            )
        )
    else:
        checks.append(_check("cloud-bridge-url", "Cloud bridge URL", "pass", "Cloud bridge URL is configured."))

    web_ui_url = _env_value(values, "HERMES_CHROME_WEB_UI_URL")
    bindings_path = _env_value(values, "HERMES_CHROME_BINDINGS_PATH")
    if web_ui_url:
        checks.append(_check("web-ui-bindings", "Profile binding source", "pass", "web-ui binding source is configured."))
    elif bindings_path:
        checks.append(
            _check(
                "web-ui-bindings",
                "Profile binding source",
                "warn",
                "file-based bindings are a development fallback; production should use HERMES_CHROME_WEB_UI_URL.",
            )
        )
    else:
        checks.append(
            _check(
                "web-ui-bindings",
                "Profile binding source",
                "fail",
                "production requires HERMES_CHROME_WEB_UI_URL for authoritative profile bindings.",
            )
        )

    public_url = _env_value(values, "HERMES_CHROME_ROUTER_PUBLIC_URL")
    public_scheme = _url_scheme(public_url)
    if not public_url:
        checks.append(
            _check(
                "router-public-url",
                "Public Router WebSocket URL",
                "fail",
                "HERMES_CHROME_ROUTER_PUBLIC_URL is required for Local Client authorization callbacks.",
            )
        )
    elif public_scheme not in {"ws", "wss"}:
        checks.append(_check("router-public-url", "Public Router WebSocket URL", "fail", "public URL must be ws(s)."))
    elif public_scheme == "ws":
        checks.append(
            _check(
                "router-public-url",
                "Public Router WebSocket URL",
                "warn",
                "plain ws is suitable for development only; production should use wss.",
            )
        )
    else:
        checks.append(_check("router-public-url", "Public Router WebSocket URL", "pass", "public wss URL is configured."))

    redis_url = _env_value(values, "HERMES_CHROME_REDIS_URL")
    if not redis_url:
        checks.append(
            _check("redis-route-table", "Redis route table", "fail", "HERMES_CHROME_REDIS_URL is required for failover.")
        )
    elif _url_scheme(redis_url) not in {"redis", "rediss"}:
        checks.append(_check("redis-route-table", "Redis route table", "fail", "Redis URL must use redis:// or rediss://."))
    else:
        checks.append(_check("redis-route-table", "Redis route table", "pass", "Redis route table is configured."))

    server_id = _env_value(values, "HERMES_CHROME_ROUTER_SERVER_ID")
    if not server_id or server_id == "local":
        checks.append(
            _check(
                "router-server-id",
                "Router serverId",
                "fail",
                "HERMES_CHROME_ROUTER_SERVER_ID must be a stable non-local id per Router instance.",
            )
        )
    else:
        checks.append(_check("router-server-id", "Router serverId", "pass", f"serverId is {server_id}."))

    raw_ttl = _env_value(values, "HERMES_CHROME_ROUTE_TTL_SECONDS") or "60"
    try:
        ttl = int(raw_ttl)
    except ValueError:
        ttl = -1
    if ttl <= 0:
        checks.append(_check("route-ttl", "Route TTL", "fail", "HERMES_CHROME_ROUTE_TTL_SECONDS must be positive."))
    elif ttl < 15 or ttl > 300:
        checks.append(_check("route-ttl", "Route TTL", "warn", "route TTL should normally stay between 15 and 300 seconds."))
    else:
        checks.append(_check("route-ttl", "Route TTL", "pass", f"route TTL is {ttl} seconds."))

    checks.append(_peer_router_check(values))

    audit_db = _env_value(values, "HERMES_CHROME_ROUTER_AUDIT_DB")
    if audit_db:
        checks.append(_check("audit-db", "Audit database", "pass", "cloud command audit database is configured."))
    else:
        checks.append(
            _check(
                "audit-db",
                "Audit database",
                "warn",
                "HERMES_CHROME_ROUTER_AUDIT_DB is recommended for production troubleshooting.",
            )
        )

    summary = {
        "pass": sum(1 for check in checks if check["status"] == "pass"),
        "warn": sum(1 for check in checks if check["status"] == "warn"),
        "fail": sum(1 for check in checks if check["status"] == "fail"),
    }
    status = "blocked" if summary["fail"] else "degraded" if summary["warn"] else "ready"
    return {"status": status, "summary": summary, "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    report = gateway_readiness_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
