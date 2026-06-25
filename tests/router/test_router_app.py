import asyncio
from aiohttp.test_utils import TestClient, TestServer

from router.app import create_router_app, dispatch_command
from router.config import RouterConfig
from router.registry import TransitCommand
from shared.protocol import CloudCommand


class FakeCloudBridge:
    def __init__(self):
        self.results = []
        self.fail_results = False

    async def post_result(self, command_id, ok, result=None, error=None):
        if self.fail_results:
            raise RuntimeError("cloud result unavailable")
        self.results.append({"id": command_id, "ok": ok, "result": result, "error": error})


class FailingWS:
    async def send_json(self, _payload):
        raise RuntimeError("websocket send failed")


class FakeBindingProvider:
    def __init__(self, bindings):
        self.bindings = bindings
        self.calls = 0

    async def load_bindings(self):
        self.calls += 1
        return dict(self.bindings)


class FakeDeviceTokenVerifier:
    def __init__(self, verified_device_id):
        self.verified_device_id = verified_device_id
        self.tokens = []

    async def verify_device_token(self, token):
        self.tokens.append(token)
        return self.verified_device_id


async def test_health_and_status(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        health = await client.get("/health")
        assert await health.json() == {"ok": True}
        status = await client.get("/status", headers={"authorization": "Bearer dev-token"})
        payload = await status.json()
        assert payload["bindings"] == {"xuxiaofeng_profile": "span-macbook"}
    finally:
        await client.close()


async def test_status_and_metrics_require_router_token_when_configured(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        status = await client.get("/status")
        metrics = await client.get("/metrics")

        assert status.status == 401
        assert await status.json() == {"error": "unauthorized"}
        assert metrics.status == 401
        assert await metrics.json() == {"error": "unauthorized"}
    finally:
        await client.close()


async def test_audit_commands_requires_router_token_and_filters_by_profile(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
        audit_db_path=tmp_path / "router-audit.sqlite3",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        unauthorized = await client.get("/audit/commands")
        assert unauthorized.status == 401

        await dispatch_command(
            app,
            CloudCommand(
                id="cmd_1",
                profile_id="xuxiaofeng_profile",
                action="page.click",
                params={"selector": "#submit", "secret": "do-not-store"},
            ),
        )
        await dispatch_command(
            app,
            CloudCommand(
                id="cmd_2",
                profile_id="other_profile",
                action="tab.list",
                params={},
            ),
        )

        response = await client.get(
            "/audit/commands?profileId=xuxiaofeng_profile&q=click",
            headers={"authorization": "Bearer dev-token"},
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["count"] == 1
        assert payload["items"][0]["commandId"] == "cmd_1"
        assert payload["items"][0]["profileId"] == "xuxiaofeng_profile"
        assert payload["items"][0]["action"] == "page.click"
        assert payload["items"][0]["status"] == "failed"
        assert "not connected" in payload["items"][0]["detail"]
        assert "paramsHash" in payload["items"][0]
        assert "do-not-store" not in str(payload["items"][0])

        app["audit_store"].record(
            command_id="cmd_3",
            profile_id="xuxiaofeng_profile",
            action="page.click",
            status="failed",
            device_id="other-mac",
            detail="other device",
        )
        by_device = await client.get(
            "/audit/commands?profileId=xuxiaofeng_profile&deviceId=span-macbook",
            headers={"authorization": "Bearer dev-token"},
        )
        by_device_payload = await by_device.json()
        assert [item["commandId"] for item in by_device_payload["items"]] == ["cmd_1"]

        app["audit_store"].record(
            command_id="cmd_4",
            profile_id="xuxiaofeng_profile",
            action="tab.list",
            status="completed",
            device_id="span-macbook",
            detail="newer event",
        )
        paged = await client.get(
            "/audit/commands?profileId=xuxiaofeng_profile&deviceId=span-macbook&limit=1&offset=1",
            headers={"authorization": "Bearer dev-token"},
        )
        paged_payload = await paged.json()
        assert paged_payload["total"] == 2
        assert paged_payload["limit"] == 1
        assert paged_payload["offset"] == 1
        assert [item["commandId"] for item in paged_payload["items"]] == ["cmd_1"]
    finally:
        await client.close()


async def test_audit_summary_reports_cross_profile_operational_counts(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"default": "span-macbook", "sales": "sales-pc"},
        audit_db_path=tmp_path / "router-audit.sqlite3",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        unauthorized = await client.get("/audit/summary")
        assert unauthorized.status == 401

        app["audit_store"].record(
            command_id="cmd_1",
            profile_id="default",
            action="page.click",
            status="failed",
            device_id="span-macbook",
            detail="browser control paused",
        )
        app["audit_store"].record(
            command_id="cmd_2",
            profile_id="default",
            action="tab.list",
            status="completed",
            device_id="span-macbook",
            detail="ok",
        )
        app["audit_store"].record(
            command_id="cmd_3",
            profile_id="sales",
            action="page.click",
            status="failed",
            device_id="sales-pc",
            detail="extension stale",
        )

        response = await client.get(
            "/audit/summary?q=click",
            headers={"authorization": "Bearer dev-token"},
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["total"] == 2
        assert payload["failed"] == 2
        assert payload["profiles"] == 2
        assert payload["devices"] == 2
        assert payload["byProfile"] == [
            {"profileId": "default", "total": 1, "failed": 1},
            {"profileId": "sales", "total": 1, "failed": 1},
        ]
        assert payload["byStatus"] == [{"status": "failed", "total": 2}]
        assert payload["byAction"] == [{"action": "page.click", "total": 2, "failed": 2}]
    finally:
        await client.close()


async def test_management_overview_requires_router_token(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/management/overview")

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
    finally:
        await client.close()


async def test_management_overview_reports_router_connections_and_bindings(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
        binding_policies={"xuxiaofeng_profile": {"local_action_mode": "confirm"}},
        min_client_version="0.2.0",
        rate_limit_per_profile=7,
        rate_limit_burst=11,
        audit_db_path=tmp_path / "router-audit.sqlite3",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.2.0",
            }
        )
        await ws.receive_json()

        response = await client.get(
            "/management/overview",
            headers={"authorization": "Bearer dev-token"},
        )

        assert response.status == 200
        payload = await response.json()
        assert payload["router"]["status"] == "healthy"
        assert isinstance(payload["router"]["now"], int)
        assert payload["router"]["minClientVersion"] == "0.2.0"
        assert payload["router"]["rateLimitPerProfile"] == 7
        assert payload["router"]["rateLimitBurst"] == 11
        assert payload["router"]["auditEnabled"] is True
        assert payload["cloudBridge"] == {
            "url": "http://cloud",
            "healthy": True,
            "lastError": None,
        }
        assert payload["connections"]["total"] == 1
        assert payload["connections"]["devices"][0]["deviceId"] == "span-macbook"
        assert payload["bindings"][0]["profileId"] == "xuxiaofeng_profile"
        assert payload["bindings"][0]["deviceId"] == "span-macbook"
        assert payload["bindings"][0]["online"] is True
        assert payload["bindings"][0]["actionPolicy"] == {"local_action_mode": "confirm"}
        assert payload["bindings"][0]["toolGate"]["enabled"] is True
        assert payload["commands"]["inFlight"] == []
        assert payload["commands"]["auditSummary"]["total"] == 0
    finally:
        await client.close()


async def test_management_overview_reports_degraded_cloud_result_channel(tmp_path):
    cloud = FakeCloudBridge()
    cloud.fail_results = True
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)

    await dispatch_command(
        app,
        CloudCommand(
            id="cmd_missing",
            profile_id="missing_profile",
            action="page.click",
            params={},
        ),
    )

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(
            "/management/overview",
            headers={"authorization": "Bearer dev-token"},
        )

        assert response.status == 200
        payload = await response.json()
        assert payload["router"]["status"] == "degraded"
        assert payload["cloudBridge"]["healthy"] is False
        assert payload["cloudBridge"]["lastError"] == "cloud result unavailable"
        assert payload["router"]["auditEnabled"] is False
        assert payload["commands"]["auditSummary"] == {
            "total": 0,
            "failed": 0,
            "profiles": 0,
            "devices": 0,
            "byProfile": [],
            "byStatus": [],
            "byAction": [],
        }
    finally:
        await client.close()


async def test_audit_time_range_filters_commands_and_summary(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"default": "span-macbook", "sales": "sales-pc"},
        audit_db_path=tmp_path / "router-audit.sqlite3",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        app["audit_store"].record(
            command_id="cmd_old",
            profile_id="default",
            action="page.click",
            status="failed",
            device_id="span-macbook",
        )
        app["audit_store"].record(
            command_id="cmd_mid",
            profile_id="default",
            action="page.click",
            status="completed",
            device_id="span-macbook",
        )
        app["audit_store"].record(
            command_id="cmd_new",
            profile_id="sales",
            action="tab.list",
            status="failed",
            device_id="sales-pc",
        )
        app["audit_store"]._db.execute(
            "UPDATE command_audit_events SET timestamp_ms = CASE command_id "
            "WHEN 'cmd_old' THEN 1717000000000 "
            "WHEN 'cmd_mid' THEN 1718000000000 "
            "WHEN 'cmd_new' THEN 1719000000000 END"
        )
        app["audit_store"]._db.commit()

        commands = await client.get(
            "/audit/commands?fromMs=1717500000000&toMs=1718500000000",
            headers={"authorization": "Bearer dev-token"},
        )
        commands_payload = await commands.json()
        assert commands_payload["total"] == 1
        assert [item["commandId"] for item in commands_payload["items"]] == ["cmd_mid"]

        summary = await client.get(
            "/audit/summary?fromMs=1717500000000&toMs=1719500000000",
            headers={"authorization": "Bearer dev-token"},
        )
        summary_payload = await summary.json()
        assert summary_payload["total"] == 2
        assert summary_payload["failed"] == 1
        assert summary_payload["profiles"] == 2
        assert summary_payload["byProfile"] == [
            {"profileId": "default", "total": 1, "failed": 0},
            {"profileId": "sales", "total": 1, "failed": 1},
        ]
    finally:
        await client.close()


async def test_metrics_exposes_router_state_for_prometheus(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={
            "xuxiaofeng_profile": "span-macbook",
            "default": "span-macbook",
        },
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    app["registry"].register("span-macbook", ("xuxiaofeng_profile",), object())
    app["registry"].add_inflight(
        TransitCommand("cmd_inflight", "xuxiaofeng_profile", "http://cloud", "page.click", {}, 28000)
    )
    app["registry"].record_command_result("xuxiaofeng_profile", ok=True)
    app["registry"].record_command_result("xuxiaofeng_profile", ok=False)
    app["registry"].record_cloud_result_error("cloud result unavailable")
    app["queues"]["xuxiaofeng_profile"].append(
        TransitCommand("cmd_queued", "xuxiaofeng_profile", "http://cloud", "page.type", {}, 28000)
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/metrics", headers={"authorization": "Bearer dev-token"})
        assert response.status == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = await response.text()
        assert "# HELP hermes_chrome_router_connected_clients" in body
        assert "hermes_chrome_router_connected_clients 1" in body
        assert "hermes_chrome_router_bound_profiles 2" in body
        assert "hermes_chrome_router_inflight_commands 1" in body
        assert "hermes_chrome_router_queued_commands 1" in body
        assert "hermes_chrome_router_cloud_result_error 1" in body
        assert 'hermes_chrome_router_profile_commands_total{profile_id="xuxiaofeng_profile"} 2' in body
        assert 'hermes_chrome_router_profile_command_errors_total{profile_id="xuxiaofeng_profile"} 1' in body
        assert 'hermes_chrome_router_profile_command_error_rate{profile_id="xuxiaofeng_profile"} 0.5' in body
        assert 'hermes_chrome_router_profile_online{profile_id="xuxiaofeng_profile",device_id="span-macbook"} 1' in body
        assert 'hermes_chrome_router_profile_online{profile_id="default",device_id="span-macbook"} 1' in body
    finally:
        await client.close()


async def test_startup_refreshes_bindings_from_provider(tmp_path):
    cloud = FakeCloudBridge()
    provider = FakeBindingProvider({"xuxiaofeng_profile": "span-macbook"})
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, binding_provider=provider, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        assert provider.calls == 1
        status = await client.get("/status", headers={"authorization": "Bearer dev-token"})
        assert (await status.json())["bindings"] == {"xuxiaofeng_profile": "span-macbook"}
    finally:
        await client.close()


async def test_startup_logs_binding_refresh_summary(tmp_path, caplog):
    cloud = FakeCloudBridge()
    provider = FakeBindingProvider({"xuxiaofeng_profile": "span-macbook"})
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, binding_provider=provider, start_poller=False)
    client = TestClient(TestServer(app))
    with caplog.at_level("INFO", logger="router.app"):
        await client.start_server()
    try:
        messages = [record.getMessage() for record in caplog.records]
        assert any("binding_refresh_success" in message for message in messages)
        assert any("binding_count=1" in message for message in messages)
    finally:
        await client.close()


async def test_refresh_bindings_endpoint_reloads_provider_snapshot(tmp_path):
    cloud = FakeCloudBridge()
    provider = FakeBindingProvider({"xuxiaofeng_profile": "span-macbook"})
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, binding_provider=provider, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        provider.bindings = {}
        response = await client.post(
            "/bindings/refresh",
            headers={"authorization": "Bearer dev-token"},
        )

        assert response.status == 200
        assert await response.json() == {"ok": True, "bindings": {}, "actionPolicies": {}}
        status = await client.get("/status", headers={"authorization": "Bearer dev-token"})
        assert (await status.json())["bindings"] == {}
        assert provider.calls == 2
    finally:
        await client.close()


async def test_refresh_bindings_endpoint_requires_router_token(tmp_path):
    cloud = FakeCloudBridge()
    provider = FakeBindingProvider({"xuxiaofeng_profile": "span-macbook"})
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, binding_provider=provider, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/bindings/refresh")

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
        assert provider.calls == 1
    finally:
        await client.close()


async def test_websocket_hello_and_result_relay(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        hello_ack = await ws.receive_json()
        assert hello_ack == {
            "type": "hello_ack",
            "deviceId": "span-macbook",
            "minClientVersion": "0.1.0",
            "bindings": [{"profileId": "xuxiaofeng_profile"}],
        }

        registry = app["registry"]
        registry.add_inflight(
            app["transit_command_factory"](
                "cmd_1",
                "xuxiaofeng_profile",
                "page.click",
                {},
                28000,
            )
        )
        await ws.send_json(
            {
                "type": "result",
                "id": "cmd_1",
                "profileId": "xuxiaofeng_profile",
                "ok": True,
                "result": {"tag": "BUTTON"},
            }
        )

        for _ in range(50):
            if cloud.results:
                break
            await asyncio.sleep(0.01)

        assert cloud.results == [
            {"id": "cmd_1", "ok": True, "result": {"tag": "BUTTON"}, "error": None}
        ]
    finally:
        await client.close()


async def test_audit_commands_records_completed_websocket_results(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
        audit_db_path=tmp_path / "router-audit.sqlite3",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()

        app["registry"].add_inflight(
            app["transit_command_factory"](
                "cmd_done",
                "xuxiaofeng_profile",
                "page.snapshot",
                {"includeText": True},
                28000,
            )
        )
        await ws.send_json(
            {
                "type": "result",
                "id": "cmd_done",
                "profileId": "xuxiaofeng_profile",
                "ok": True,
                "result": {"title": "Hermes"},
            }
        )

        for _ in range(50):
            if cloud.results:
                break
            await asyncio.sleep(0.01)

        response = await client.get(
            "/audit/commands?status=completed&commandId=cmd_done",
            headers={"authorization": "Bearer dev-token"},
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["count"] == 1
        assert payload["items"][0]["commandId"] == "cmd_done"
        assert payload["items"][0]["action"] == "page.snapshot"
        assert payload["items"][0]["status"] == "completed"
        assert payload["items"][0]["paramsHash"]
    finally:
        await client.close()


async def test_websocket_heartbeat_updates_connection_status(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        assert await ws.receive_json() == {
            "type": "hello_ack",
            "deviceId": "span-macbook",
            "minClientVersion": "0.1.0",
            "bindings": [{"profileId": "xuxiaofeng_profile"}],
        }

        await ws.send_json(
            {
                "type": "heartbeat",
                "timestamp": 1718500000000,
                "extensionConnected": True,
                "extensionVersion": "0.15.38",
            }
        )
        assert await ws.receive_json() == {
            "type": "heartbeat_ack",
            "timestamp": 1718500000000,
        }

        status = await client.get("/status", headers={"authorization": "Bearer dev-token"})
        details = (await status.json())["connectionDetails"]["span-macbook"]
        assert details["extensionConnected"] is True
        assert details["extensionVersion"] == "0.15.38"
        assert details["lastHeartbeatAt"] is not None
    finally:
        await client.close()


async def test_profile_status_reports_binding_connection_and_extension(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()
        await ws.send_json(
            {
                "type": "heartbeat",
                "timestamp": 1718500000000,
                "extensionConnected": True,
                "extensionVersion": "0.15.38",
            }
        )
        await ws.receive_json()

        status = await client.get(
            "/status/profile/xuxiaofeng_profile",
            headers={"authorization": "Bearer dev-token"},
        )
        payload = await status.json()

        assert payload == {
            "profileId": "xuxiaofeng_profile",
            "deviceId": "span-macbook",
            "online": True,
            "extensionConnected": True,
            "extensionVersion": "0.15.38",
            "lastHeartbeatAt": payload["lastHeartbeatAt"],
            "lastCloudResultError": None,
            "actionPolicy": {},
            "commandStats": {
                "total": 0,
                "success": 0,
                "failure": 0,
                "errorRate": 0,
            },
            "toolGate": {
                "enabled": True,
                "reason": "profile browser binding and Local Client are ready",
            },
            "items": [
                {
                    "key": "binding",
                    "status": "pass",
                    "title": "Browser binding is configured",
                    "detail": "profile xuxiaofeng_profile is bound to device span-macbook",
                },
                {
                    "key": "client",
                    "status": "pass",
                    "title": "Hermes Local Client is connected",
                    "detail": "device span-macbook is online",
                },
                {
                    "key": "extension",
                    "status": "pass",
                    "title": "Chrome extension is connected",
                    "detail": "version 0.15.38",
                },
                {
                    "key": "cloud_bridge",
                    "status": "pass",
                    "title": "Cloud Chrome bridge result channel is healthy",
                },
            ],
        }
        assert payload["lastHeartbeatAt"] is not None
    finally:
        await client.close()


async def test_profile_status_tool_gate_blocks_denied_action_policy(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
        binding_policies={"xuxiaofeng_profile": {"local_action_mode": "deny"}},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()

        status = await client.get(
            "/status/profile/xuxiaofeng_profile",
            headers={"authorization": "Bearer dev-token"},
        )
        payload = await status.json()

        assert payload["actionPolicy"] == {"local_action_mode": "deny"}
        assert payload["toolGate"] == {
            "enabled": False,
            "reason": "profile action policy denies Chrome control",
        }
    finally:
        await client.close()


async def test_profile_status_rejects_missing_router_token(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        status = await client.get("/status/profile/xuxiaofeng_profile")

        assert status.status == 401
        assert await status.json() == {"error": "unauthorized"}
    finally:
        await client.close()


async def test_websocket_rejects_bad_token(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "wrong",
                "clientVersion": "0.1.0",
            }
        )
        payload = await ws.receive_json()
        assert payload["type"] == "error"
        assert "unauthorized" in payload["error"]
    finally:
        await client.close()


async def test_websocket_rejects_old_client_version(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
        min_client_version="0.2.0",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        payload = await ws.receive_json()
        assert payload == {
            "type": "error",
            "error": "version_too_old",
            "minClientVersion": "0.2.0",
        }
    finally:
        await client.close()


async def test_websocket_accepts_verified_device_token(tmp_path):
    cloud = FakeCloudBridge()
    verifier = FakeDeviceTokenVerifier("span-macbook")
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(
        config,
        cloud_bridge=cloud,
        device_token_verifier=verifier,
        start_poller=False,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "hclc_device_token",
                "clientVersion": "0.1.0",
            }
        )
        assert await ws.receive_json() == {
            "type": "hello_ack",
            "deviceId": "span-macbook",
            "minClientVersion": "0.1.0",
            "bindings": [{"profileId": "xuxiaofeng_profile"}],
        }
        assert verifier.tokens == ["hclc_device_token"]
    finally:
        await client.close()


async def test_websocket_rejects_verified_token_for_different_device(tmp_path):
    cloud = FakeCloudBridge()
    verifier = FakeDeviceTokenVerifier("other-device")
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(
        config,
        cloud_bridge=cloud,
        device_token_verifier=verifier,
        start_poller=False,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "hclc_device_token",
                "clientVersion": "0.1.0",
            }
        )
        payload = await ws.receive_json()
        assert payload["type"] == "error"
        assert "unauthorized" in payload["error"]
    finally:
        await client.close()


async def test_dispatch_no_binding_posts_cloud_error(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)

    await dispatch_command(
        app,
        CloudCommand(
            id="cmd_missing",
            profile_id="missing_profile",
            action="page.click",
            params={},
        ),
    )

    assert cloud.results == [
        {
            "id": "cmd_missing",
            "ok": False,
            "result": None,
            "error": "browser binding not found for profile missing_profile",
        }
    ]


async def test_dispatch_rate_limit_posts_cloud_error(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
        rate_limit_per_profile=1,
        rate_limit_burst=1,
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    app["registry"].register("span-macbook", ("xuxiaofeng_profile",), FailingWS())

    await dispatch_command(
        app,
        CloudCommand(
            id="cmd_1",
            profile_id="xuxiaofeng_profile",
            action="page.snapshot",
            params={},
        ),
    )
    await dispatch_command(
        app,
        CloudCommand(
            id="cmd_2",
            profile_id="xuxiaofeng_profile",
            action="page.click",
            params={},
        ),
    )

    assert cloud.results[-1] == {
        "id": "cmd_2",
        "ok": False,
        "result": None,
        "error": "rate limit exceeded for profile xuxiaofeng_profile",
    }


async def test_cloud_result_failure_is_exposed_in_status(tmp_path):
    cloud = FakeCloudBridge()
    cloud.fail_results = True
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)

    await dispatch_command(
        app,
        CloudCommand(
            id="cmd_missing",
            profile_id="missing_profile",
            action="page.click",
            params={},
        ),
    )

    assert "cloud result unavailable" in app["registry"].status()["lastCloudResultError"]


async def test_result_profile_mismatch_posts_cloud_error(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()
        app["registry"].add_inflight(
            TransitCommand(
                id="cmd_1",
                profile_id="xuxiaofeng_profile",
                cloud_bridge_url="http://cloud",
                action="page.click",
                params={},
                timeout_ms=28000,
            )
        )
        app["current_by_profile"]["xuxiaofeng_profile"] = "cmd_1"

        await ws.send_json(
            {
                "type": "result",
                "id": "cmd_1",
                "profileId": "wrong_profile",
                "ok": True,
                "result": {},
            }
        )

        for _ in range(50):
            if cloud.results:
                break
            await asyncio.sleep(0.01)

        assert cloud.results == [
            {
                "id": "cmd_1",
                "ok": False,
                "result": None,
                "error": (
                    "result profileId mismatch for command cmd_1: "
                    "expected xuxiaofeng_profile, got wrong_profile"
                ),
            }
        ]
    finally:
        await client.close()


async def test_dispatch_serializes_same_profile_until_result(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()

        await dispatch_command(
            app,
            CloudCommand("cmd_1", "xuxiaofeng_profile", "page.click", {}),
        )
        first = await ws.receive_json()
        await dispatch_command(
            app,
            CloudCommand("cmd_2", "xuxiaofeng_profile", "page.type", {}),
        )

        assert first["id"] == "cmd_1"
        assert app["current_by_profile"] == {"xuxiaofeng_profile": "cmd_1"}
        assert [item.id for item in app["queues"]["xuxiaofeng_profile"]] == ["cmd_2"]
    finally:
        await client.close()


async def test_delivery_timeout_posts_cloud_error_and_advances_queue(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()

        app["queues"]["xuxiaofeng_profile"].append(
            TransitCommand("cmd_timeout", "xuxiaofeng_profile", "http://cloud", "page.click", {}, 1)
        )
        from router.app import _dispatch_next_for_profile

        await _dispatch_next_for_profile(app, "xuxiaofeng_profile")

        for _ in range(50):
            if cloud.results:
                break
            await asyncio.sleep(0.01)

        assert cloud.results == [
            {
                "id": "cmd_timeout",
                "ok": False,
                "result": None,
                "error": "browser bridge timed out for profile xuxiaofeng_profile",
            }
        ]
        assert app["current_by_profile"] == {}
    finally:
        await client.close()


async def test_dispatch_send_failure_cleans_state_and_advances_queue(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    app["registry"].register("span-macbook", ("xuxiaofeng_profile",), FailingWS())
    app["queues"]["xuxiaofeng_profile"].append(
        TransitCommand("cmd_send_fail", "xuxiaofeng_profile", "http://cloud", "page.click", {}, 28000)
    )

    from router.app import _dispatch_next_for_profile

    await _dispatch_next_for_profile(app, "xuxiaofeng_profile")

    assert cloud.results == [
        {
            "id": "cmd_send_fail",
            "ok": False,
            "result": None,
            "error": "websocket send failed",
        }
    ]
    assert app["registry"].status()["inFlight"] == []
    assert app["current_by_profile"] == {}


async def test_websocket_disconnect_fails_inflight_commands(tmp_path):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/bridge")
        await ws.send_json(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "clientVersion": "0.1.0",
            }
        )
        await ws.receive_json()
        app["registry"].add_inflight(
            TransitCommand(
                id="cmd_1",
                profile_id="xuxiaofeng_profile",
                cloud_bridge_url="http://cloud",
                action="page.click",
                params={},
                timeout_ms=28000,
            )
        )
        await ws.close()

        for _ in range(50):
            if cloud.results:
                break
            await asyncio.sleep(0.01)

        assert cloud.results == [
            {
                "id": "cmd_1",
                "ok": False,
                "result": None,
                "error": "browser bridge disconnected for profile xuxiaofeng_profile",
            }
        ]
    finally:
        await client.close()
