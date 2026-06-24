from __future__ import annotations

from router.gateway_readiness import gateway_readiness_report


def test_gateway_readiness_reports_ready_for_sticky_failover_configuration():
    report = gateway_readiness_report(
        {
            "HERMES_CHROME_ROUTER_TOKEN": "prod-router-token",
            "HERMES_CHROME_CLOUD_BRIDGE_URL": "https://web-ui.internal/chrome-bridge",
            "HERMES_CHROME_WEB_UI_URL": "https://hermes.example.com",
            "HERMES_CHROME_ROUTER_PUBLIC_URL": "wss://router.example.com/bridge",
            "HERMES_CHROME_REDIS_URL": "redis://redis.internal:6379/0",
            "HERMES_CHROME_ROUTER_SERVER_ID": "router-a",
            "HERMES_CHROME_ROUTE_TTL_SECONDS": "60",
            "HERMES_CHROME_ROUTER_PEERS": '{"router-b":"http://router-b.internal:8787"}',
            "HERMES_CHROME_ROUTER_AUDIT_DB": "/var/lib/hermes/chrome-router-audit.sqlite3",
        }
    )

    assert report["status"] == "ready"
    assert report["summary"] == {"pass": 9, "warn": 0, "fail": 0}
    assert [check["id"] for check in report["checks"]] == [
        "router-token",
        "cloud-bridge-url",
        "web-ui-bindings",
        "router-public-url",
        "redis-route-table",
        "router-server-id",
        "route-ttl",
        "peer-routers",
        "audit-db",
    ]


def test_gateway_readiness_blocks_missing_production_sticky_failover_settings():
    report = gateway_readiness_report(
        {
            "HERMES_CHROME_ROUTER_TOKEN": "dev-token",
            "HERMES_CHROME_CLOUD_BRIDGE_URL": "http://127.0.0.1:16319",
            "HERMES_CHROME_ROUTER_SERVER_ID": "local",
            "HERMES_CHROME_ROUTE_TTL_SECONDS": "0",
            "HERMES_CHROME_ROUTER_PEERS": '{"local":"http://127.0.0.1:8787"}',
        }
    )

    assert report["status"] == "blocked"
    failed = {check["id"]: check for check in report["checks"] if check["status"] == "fail"}
    assert "router-public-url" in failed
    assert "redis-route-table" in failed
    assert "router-server-id" in failed
    assert "route-ttl" in failed
    assert "peer-routers" in failed
    assert "web-ui-bindings" in failed
    assert "dev fallback token" in failed["router-token"]["detail"]
