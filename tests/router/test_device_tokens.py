from aiohttp import web

from router.device_tokens import WebUIDeviceTokenVerifier


async def test_webui_device_token_verifier_posts_bearer_token(aiohttp_server):
    seen = {}

    async def verify(request):
        seen["authorization"] = request.headers.get("authorization")
        return web.json_response({"ok": True, "device_id": "span-macbook"})

    app = web.Application()
    app.router.add_post("/api/devices/bridge-token/verify", verify)
    server = await aiohttp_server(app)

    verifier = WebUIDeviceTokenVerifier(f"http://{server.host}:{server.port}")

    assert await verifier.verify_device_token("hclc_token") == "span-macbook"
    assert seen == {"authorization": "Bearer hclc_token"}


async def test_webui_device_token_verifier_rejects_failed_verification(aiohttp_server):
    async def verify(_request):
        return web.json_response({"ok": False, "error": "Invalid device token"}, status=401)

    app = web.Application()
    app.router.add_post("/api/devices/bridge-token/verify", verify)
    server = await aiohttp_server(app)

    verifier = WebUIDeviceTokenVerifier(f"http://{server.host}:{server.port}")

    assert await verifier.verify_device_token("hclc_token") is None
