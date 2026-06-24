from aiohttp import web

from router.cloud_bridge import CloudBridgeClient


async def test_cloud_bridge_client_sends_bearer_token_for_next_and_result(aiohttp_server):
    seen_headers = []

    async def next_handler(request):
        seen_headers.append(("next", request.headers.get("authorization")))
        return web.json_response(
            {
                "type": "command",
                "command": {
                    "id": "cmd_1",
                    "profileId": "xuxiaofeng_profile",
                    "action": "tab.list",
                    "params": {},
                },
                "expectedExtensionVersion": "0.15.38",
            }
        )

    async def result_handler(request):
        seen_headers.append(("result", request.headers.get("authorization")))
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/next", next_handler)
    app.router.add_post("/result", result_handler)
    server = await aiohttp_server(app)

    client = CloudBridgeClient(f"http://{server.host}:{server.port}", token="router-secret")
    command = await client.poll_next("router-1")
    await client.post_result(command.id, True, result={"tabs": []})

    assert seen_headers == [
        ("next", "Bearer router-secret"),
        ("result", "Bearer router-secret"),
    ]
