import asyncio
import contextlib
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from harness.bridge import LocalBridge
from harness.ws_client import RouterWebSocketClient
from router.app import create_router_app
from router.config import RouterConfig


async def test_fake_cloud_router_local_bridge_extension_loop(aiohttp_server, tmp_path):
    cloud_results = []
    commands = asyncio.Queue()

    async def cloud_next(_request):
        try:
            return web.json_response(commands.get_nowait())
        except asyncio.QueueEmpty:
            return web.json_response({"type": "none", "expectedExtensionVersion": "0.15.38"})

    async def cloud_result(request):
        cloud_results.append(await request.json())
        return web.json_response({"ok": True})

    cloud_app = web.Application()
    cloud_app.router.add_get("/next", cloud_next)
    cloud_app.router.add_post("/result", cloud_result)
    cloud_server = await aiohttp_server(cloud_app)

    bindings = tmp_path / "bindings.json"
    bindings.write_text(
        json.dumps(
            {
                "bindings": [
                    {"profileId": "xuxiaofeng_profile", "deviceId": "span-macbook"}
                ]
            }
        )
    )
    router_config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url=f"http://{cloud_server.host}:{cloud_server.port}",
        router_token="dev-token",
        bindings_path=bindings,
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    router_app = create_router_app(router_config, start_poller=True)
    router_client = TestClient(TestServer(router_app))
    await router_client.start_server()

    ws_client = RouterWebSocketClient(
        router_url=f"ws://{router_client.host}:{router_client.port}/bridge",
        token="dev-token",
        device_id="span-macbook",
        profile_ids=("xuxiaofeng_profile",),
        local_bridge=None,
    )
    local_bridge = LocalBridge(result_sink=ws_client, long_poll_seconds=0.01)
    ws_client.local_bridge = local_bridge
    local_client = TestClient(TestServer(local_bridge.create_app()))
    await local_client.start_server()
    ws_task = asyncio.create_task(ws_client.run_once())

    try:
        for _ in range(50):
            status = await router_client.get("/status", headers={"authorization": "Bearer dev-token"})
            payload = await status.json()
            if payload["connections"]:
                break
            await asyncio.sleep(0.02)

        await commands.put(
            {
                "type": "command",
                "command": {
                    "id": "cmd_1",
                    "profileId": "xuxiaofeng_profile",
                    "action": "page.click",
                    "params": {"uid": "el-3"},
                },
                "expectedExtensionVersion": "0.15.38",
            }
        )

        next_payload = {"type": "none"}
        for _ in range(50):
            next_resp = await local_client.get(
                "/next?name=fake-extension",
                headers={"origin": "chrome-extension://abc"},
            )
            next_payload = await next_resp.json()
            if next_payload["type"] == "command":
                break
            await asyncio.sleep(0.02)

        assert next_payload["command"]["id"] == "cmd_1"
        result_resp = await local_client.post(
            "/result",
            json={"id": "cmd_1", "ok": True, "result": {"tag": "BUTTON"}},
            headers={"origin": "chrome-extension://abc"},
        )
        assert result_resp.status == 200

        for _ in range(50):
            if cloud_results:
                break
            await asyncio.sleep(0.02)

        assert cloud_results == [{"id": "cmd_1", "ok": True, "result": {"tag": "BUTTON"}}]
    finally:
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        await local_client.close()
        await router_client.close()


async def test_basic_tab_snapshot_click_commands_complete_end_to_end(aiohttp_server, tmp_path):
    cloud_results = []
    commands = asyncio.Queue()
    router_client = await _build_router_with_cloud(aiohttp_server, tmp_path, commands, cloud_results)
    local_client, ws_task, _ws_client = await _connect_local_client(router_client)
    command_cases = [
        (
            "cmd_tab_list",
            "tab.list",
            {},
            [{"id": "tab-1", "title": "Hermes", "url": "https://hermes.local"}],
        ),
        (
            "cmd_snapshot",
            "page.snapshot",
            {"mode": "interactive", "maxElements": 20},
            {"title": "Hermes", "elements": [{"uid": "el-3", "role": "button"}]},
        ),
        (
            "cmd_click",
            "page.click",
            {"uid": "el-3"},
            {"tag": "BUTTON", "clicked": True},
        ),
    ]

    try:
        for command_id, action, params, _result in command_cases:
            await commands.put(
                {
                    "type": "command",
                    "command": {
                        "id": command_id,
                        "profileId": "xuxiaofeng_profile",
                        "action": action,
                        "params": params,
                    },
                    "expectedExtensionVersion": "0.15.38",
                }
            )

        delivered = []
        for command_id, action, params, result in command_cases:
            next_payload = {"type": "none"}
            for _attempt in range(50):
                next_resp = await local_client.get(
                    "/next?name=fake-extension",
                    headers={"origin": "chrome-extension://abc"},
                )
                next_payload = await next_resp.json()
                if next_payload["type"] == "command":
                    break
                await asyncio.sleep(0.01)
            assert next_payload["type"] == "command"
            assert next_payload["command"]["id"] == command_id
            assert next_payload["command"]["profileId"] == "xuxiaofeng_profile"
            assert next_payload["command"]["action"] == action
            assert next_payload["command"]["params"] == params
            delivered.append(next_payload["command"]["id"])

            result_resp = await local_client.post(
                "/result",
                json={"id": command_id, "ok": True, "result": result},
                headers={"origin": "chrome-extension://abc"},
            )
            assert result_resp.status == 200

        for _attempt in range(100):
            if len(cloud_results) == len(command_cases):
                break
            await asyncio.sleep(0.01)

        assert delivered == [case[0] for case in command_cases]
        assert cloud_results == [
            {"id": command_id, "ok": True, "result": result}
            for command_id, _action, _params, result in command_cases
        ]
    finally:
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        await local_client.close()
        await router_client.close()


async def _build_router_with_cloud(aiohttp_server, tmp_path, commands, cloud_results):
    async def cloud_next(_request):
        try:
            return web.json_response(commands.get_nowait())
        except asyncio.QueueEmpty:
            return web.json_response({"type": "none", "expectedExtensionVersion": "0.15.38"})

    async def cloud_result(request):
        cloud_results.append(await request.json())
        return web.json_response({"ok": True})

    cloud_app = web.Application()
    cloud_app.router.add_get("/next", cloud_next)
    cloud_app.router.add_post("/result", cloud_result)
    cloud_server = await aiohttp_server(cloud_app)
    bindings = tmp_path / "bindings.json"
    bindings.write_text(
        json.dumps(
            {
                "bindings": [
                    {"profileId": "xuxiaofeng_profile", "deviceId": "span-macbook"}
                ]
            }
        )
    )
    router_config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url=f"http://{cloud_server.host}:{cloud_server.port}",
        router_token="dev-token",
        bindings_path=bindings,
        bindings={"xuxiaofeng_profile": "span-macbook"},
    )
    router_app = create_router_app(router_config, start_poller=True)
    router_client = TestClient(TestServer(router_app))
    await router_client.start_server()
    return router_client


async def _connect_local_client(router_client, *, long_poll_seconds=0.01):
    ws_client = RouterWebSocketClient(
        router_url=f"ws://{router_client.host}:{router_client.port}/bridge",
        token="dev-token",
        device_id="span-macbook",
        profile_ids=("xuxiaofeng_profile",),
        local_bridge=None,
    )
    local_bridge = LocalBridge(result_sink=ws_client, long_poll_seconds=long_poll_seconds)
    ws_client.local_bridge = local_bridge
    local_client = TestClient(TestServer(local_bridge.create_app()))
    await local_client.start_server()
    ws_task = asyncio.create_task(ws_client.run_once())
    for _ in range(50):
        status = await router_client.get("/status", headers={"authorization": "Bearer dev-token"})
        payload = await status.json()
        if payload["connections"]:
            break
        await asyncio.sleep(0.02)
    return local_client, ws_task, ws_client


async def test_fake_cloud_missing_profile_id_fails_safely(aiohttp_server, tmp_path):
    cloud_results = []
    commands = asyncio.Queue()
    await commands.put(
        {
            "type": "command",
            "command": {"id": "cmd_bad", "action": "page.click", "params": {}},
        }
    )
    router_client = await _build_router_with_cloud(aiohttp_server, tmp_path, commands, cloud_results)
    try:
        for _ in range(50):
            status = await router_client.get("/status", headers={"authorization": "Bearer dev-token"})
            payload = await status.json()
            if payload["lastCloudResultError"]:
                break
            await asyncio.sleep(0.02)
        assert cloud_results == [
            {"id": "cmd_bad", "ok": False, "error": "missing or invalid profileId"}
        ]
    finally:
        await router_client.close()


async def test_fake_extension_not_polling_returns_cloud_error(aiohttp_server, tmp_path, monkeypatch):
    monkeypatch.setattr("harness.bridge.LOCAL_COMMAND_TIMEOUT_MS", 1)
    cloud_results = []
    commands = asyncio.Queue()
    router_client = await _build_router_with_cloud(aiohttp_server, tmp_path, commands, cloud_results)
    local_client, ws_task, _ws_client = await _connect_local_client(router_client)
    try:
        await commands.put(
            {
                "type": "command",
                "command": {
                    "id": "cmd_timeout",
                    "profileId": "xuxiaofeng_profile",
                    "action": "page.click",
                    "params": {},
                },
                "expectedExtensionVersion": "0.15.38",
            }
        )
        for _ in range(100):
            if cloud_results:
                break
            await asyncio.sleep(0.01)
        assert cloud_results == [
            {
                "id": "cmd_timeout",
                "ok": False,
                "error": "Chrome extension not polling for command cmd_timeout",
            }
        ]
    finally:
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        await local_client.close()
        await router_client.close()


async def test_router_disconnect_returns_cloud_error(aiohttp_server, tmp_path):
    cloud_results = []
    commands = asyncio.Queue()
    router_client = await _build_router_with_cloud(aiohttp_server, tmp_path, commands, cloud_results)
    local_client, ws_task, ws_client = await _connect_local_client(router_client)
    try:
        await commands.put(
            {
                "type": "command",
                "command": {
                    "id": "cmd_disconnect",
                    "profileId": "xuxiaofeng_profile",
                    "action": "page.click",
                    "params": {},
                },
                "expectedExtensionVersion": "0.15.38",
            }
        )
        for _ in range(50):
            next_resp = await local_client.get(
                "/next?name=fake-extension",
                headers={"origin": "chrome-extension://abc"},
            )
            next_payload = await next_resp.json()
            if next_payload["type"] == "command":
                break
            await asyncio.sleep(0.01)
        assert next_payload["type"] == "command"
        await ws_client.close()
        try:
            await asyncio.wait_for(ws_task, timeout=1)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task
        for _ in range(100):
            if cloud_results:
                break
            await asyncio.sleep(0.01)
        assert cloud_results == [
            {
                "id": "cmd_disconnect",
                "ok": False,
                "error": "browser bridge disconnected for profile xuxiaofeng_profile",
            }
        ]
    finally:
        await local_client.close()
        await router_client.close()


async def test_same_profile_two_commands_are_delivered_in_order(aiohttp_server, tmp_path):
    cloud_results = []
    commands = asyncio.Queue()
    router_client = await _build_router_with_cloud(aiohttp_server, tmp_path, commands, cloud_results)
    local_client, ws_task, _ws_client = await _connect_local_client(router_client)
    try:
        for command_id, action in (("cmd_1", "page.click"), ("cmd_2", "page.type")):
            await commands.put(
                {
                    "type": "command",
                    "command": {
                        "id": command_id,
                        "profileId": "xuxiaofeng_profile",
                        "action": action,
                        "params": {},
                    },
                    "expectedExtensionVersion": "0.15.38",
                }
            )
        seen = []
        for _ in range(2):
            for _attempt in range(50):
                next_resp = await local_client.get(
                    "/next?name=fake-extension",
                    headers={"origin": "chrome-extension://abc"},
                )
                next_payload = await next_resp.json()
                if next_payload["type"] == "command":
                    break
                await asyncio.sleep(0.01)
            command_id = next_payload["command"]["id"]
            seen.append(command_id)
            await local_client.post(
                "/result",
                json={"id": command_id, "ok": True, "result": {"id": command_id}},
                headers={"origin": "chrome-extension://abc"},
            )

        assert seen == ["cmd_1", "cmd_2"]
    finally:
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        await local_client.close()
        await router_client.close()
