import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from harness.bridge import LocalBridge
from harness.ws_client import RouterWebSocketClient


class ResultSink:
    def __init__(self):
        self.results = []

    async def send_result(self, payload):
        self.results.append(payload)


async def test_next_returns_none_with_version_header():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, extension_version="0.15.38", long_poll_seconds=0.01)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        resp = await client.get(
            "/next?name=test-extension",
            headers={"origin": "chrome-extension://abc"},
        )
        payload = await resp.json()
        assert payload == {"type": "none", "expectedExtensionVersion": "0.15.38"}
        assert resp.headers["x-hermes-chrome-version"] == "0.15.38"
    finally:
        await client.close()


async def test_next_delivers_command_and_result_relays_profile_id():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, extension_version="0.15.38")
    await bridge.enqueue_command(
        {
            "type": "command",
            "id": "cmd_1",
            "profileId": "xuxiaofeng_profile",
            "action": "page.click",
            "params": {"uid": "el-3"},
            "timeoutMs": 25000,
        }
    )
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        next_resp = await client.get(
            "/next?name=test-extension",
            headers={"origin": "chrome-extension://abc"},
        )
        next_payload = await next_resp.json()
        assert next_payload["command"]["id"] == "cmd_1"
        assert next_payload["command"]["profileId"] == "xuxiaofeng_profile"

        result_resp = await client.post(
            "/result",
            json={"id": "cmd_1", "ok": True, "result": {"tag": "BUTTON"}},
            headers={"origin": "chrome-extension://abc"},
        )
        assert await result_resp.json() == {"ok": True}
        assert sink.results == [
            {
                "type": "result",
                "id": "cmd_1",
                "profileId": "xuxiaofeng_profile",
                "ok": True,
                "result": {"tag": "BUTTON"},
            }
        ]
    finally:
        await client.close()


async def test_result_unknown_id_returns_404():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, long_poll_seconds=0.01)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/result",
            json={"id": "missing", "ok": True, "result": {}},
            headers={"origin": "chrome-extension://abc"},
        )
        assert resp.status == 404
        assert await resp.json() == {"ok": False, "error": "unknown command id"}
    finally:
        await client.close()


async def test_next_rejects_non_extension_origin():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, long_poll_seconds=0.01)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        resp = await client.get("/next", headers={"origin": "https://evil.example"})
        assert resp.status == 403
    finally:
        await client.close()


async def test_result_rejects_non_extension_origin():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, long_poll_seconds=0.01)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/result",
            json={"id": "cmd_1", "ok": True, "result": {}},
            headers={"origin": "https://evil.example"},
        )
        assert resp.status == 403
    finally:
        await client.close()


async def test_command_accepts_local_process_request():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/command",
            json={
                "type": "command",
                "id": "cmd_local",
                "profileId": "xuxiaofeng_profile",
                "action": "page.click",
                "params": {},
                "timeoutMs": 25000,
            },
        )
        assert resp.status == 200
        assert bridge._queue[0]["id"] == "cmd_local"
    finally:
        await client.close()


async def test_command_rejects_browser_origin():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/command",
            json={"id": "cmd_local"},
            headers={"origin": "chrome-extension://abc"},
        )
        assert resp.status == 403
    finally:
        await client.close()


async def test_status_reflects_extension_poll():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, long_poll_seconds=0.01)
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        await client.get(
            "/next?name=test-extension",
            headers={"origin": "chrome-extension://abc"},
        )
        resp = await client.get("/status")
        payload = await resp.json()
        assert payload["connected"] is True
        assert payload["clientName"] == "test-extension"
    finally:
        await client.close()


async def test_queued_command_timeout_reports_not_polling():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    await bridge.enqueue_command(
        {
            "type": "command",
            "id": "cmd_timeout",
            "profileId": "xuxiaofeng_profile",
            "action": "page.click",
            "params": {},
            "timeoutMs": 1,
        }
    )

    for _ in range(50):
        if sink.results:
            break
        import asyncio

        await asyncio.sleep(0.01)

    assert sink.results == [
        {
            "type": "result",
            "id": "cmd_timeout",
            "profileId": "xuxiaofeng_profile",
            "ok": False,
            "error": "Chrome extension not polling for command cmd_timeout",
        }
    ]


async def test_router_timeout_is_clamped_to_local_command_timeout(monkeypatch):
    monkeypatch.setattr(
        "harness.bridge.LOCAL_COMMAND_TIMEOUT_MS",
        1,
    )
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    await bridge.enqueue_command(
        {
            "type": "command",
            "id": "cmd_timeout",
            "profileId": "xuxiaofeng_profile",
            "action": "page.click",
            "params": {},
            "timeoutMs": 28000,
        }
    )

    import asyncio

    for _ in range(50):
        if sink.results:
            break
        await asyncio.sleep(0.01)

    assert sink.results[0]["id"] == "cmd_timeout"
    assert sink.results[0]["ok"] is False


async def test_delivered_command_timeout_reports_no_result():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink, long_poll_seconds=0.01)
    await bridge.enqueue_command(
        {
            "type": "command",
            "id": "cmd_timeout",
            "profileId": "xuxiaofeng_profile",
            "action": "page.click",
            "params": {},
            "timeoutMs": 1,
        }
    )
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    try:
        await client.get(
            "/next?name=test-extension",
            headers={"origin": "chrome-extension://abc"},
        )
        for _ in range(50):
            if sink.results:
                break
            import asyncio

            await asyncio.sleep(0.01)

        assert sink.results == [
            {
                "type": "result",
                "id": "cmd_timeout",
                "profileId": "xuxiaofeng_profile",
                "ok": False,
                "error": "Chrome extension did not return a result for command cmd_timeout",
            }
        ]
    finally:
        await client.close()


async def test_ws_client_sends_hello_and_enqueues_command(aiohttp_server):
    received = {}

    async def bridge_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await ws.receive_json()
        received["hello"] = hello
        await ws.send_json({"type": "hello_ack", "deviceId": hello["deviceId"]})
        await ws.send_json(
            {
                "type": "command",
                "id": "cmd_1",
                "profileId": "xuxiaofeng_profile",
                "action": "page.click",
                "params": {},
                "timeoutMs": 25000,
            }
        )
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/bridge", bridge_ws)
    server = await aiohttp_server(app)

    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    client = RouterWebSocketClient(
        router_url=f"ws://{server.host}:{server.port}/bridge",
        token="dev-token",
        device_id="span-macbook",
        profile_ids=("xuxiaofeng_profile",),
        local_bridge=bridge,
    )

    await client.run_once()

    assert received["hello"]["type"] == "hello"
    assert received["hello"]["deviceId"] == "span-macbook"
    assert sink.results == [
        {
            "type": "result",
            "id": "cmd_1",
            "profileId": "xuxiaofeng_profile",
            "ok": False,
            "error": "Router connection closed",
        }
    ]


async def test_ws_client_sends_heartbeat(aiohttp_server):
    heartbeats = []

    async def bridge_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await ws.receive_json()
        await ws.send_json({"type": "hello_ack", "deviceId": hello["deviceId"]})
        heartbeat = await ws.receive_json()
        heartbeats.append(heartbeat)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/bridge", bridge_ws)
    server = await aiohttp_server(app)

    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    client = RouterWebSocketClient(
        router_url=f"ws://{server.host}:{server.port}/bridge",
        token="dev-token",
        device_id="span-macbook",
        profile_ids=("xuxiaofeng_profile",),
        local_bridge=bridge,
        heartbeat_interval=0.01,
    )

    await client.run_once()

    assert heartbeats
    assert heartbeats[0]["type"] == "heartbeat"
    assert heartbeats[0]["extensionConnected"] is False


async def test_ws_client_close_stops_reconnect_loop(aiohttp_server):
    connections = 0

    async def bridge_ws(request):
        nonlocal connections
        connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await ws.receive_json()
        await ws.send_json({"type": "hello_ack", "deviceId": hello["deviceId"]})
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/bridge", bridge_ws)
    server = await aiohttp_server(app)
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    client = RouterWebSocketClient(
        router_url=f"ws://{server.host}:{server.port}/bridge",
        token="dev-token",
        device_id="span-macbook",
        profile_ids=("xuxiaofeng_profile",),
        local_bridge=bridge,
        reconnect_delay=0.01,
    )
    task = asyncio.create_task(client.run_forever())
    await asyncio.sleep(0.03)
    await client.close()
    await asyncio.wait_for(task, timeout=1)
    current_connections = connections
    await asyncio.sleep(0.03)
    assert connections == current_connections


async def test_local_bridge_cleanup_cancels_timeout_tasks():
    sink = ResultSink()
    bridge = LocalBridge(result_sink=sink)
    await bridge.enqueue_command(
        {
            "type": "command",
            "id": "cmd_timeout",
            "profileId": "xuxiaofeng_profile",
            "action": "page.click",
            "params": {},
            "timeoutMs": 28000,
        }
    )
    client = TestClient(TestServer(bridge.create_app()))
    await client.start_server()
    await client.close()
    assert bridge._timeout_tasks == {}
