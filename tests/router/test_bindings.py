from aiohttp import web

from router.bindings import WebUIBindingProvider


async def test_webui_binding_provider_loads_enabled_profile_device_bindings(aiohttp_server):
    seen_headers = []

    async def profile_bindings(request):
        seen_headers.append(request.headers.get("authorization"))
        return web.json_response(
            {
                "bindings": [
                    {
                        "profile_id": "xuxiaofeng_profile",
                        "device_id": "span-macbook",
                        "enabled": True,
                        "action_policy": {"local_action_mode": "confirm"},
                    },
                    {
                        "profile_id": "disabled_profile",
                        "device_id": "lab-mac",
                        "enabled": False,
                    },
                    {
                        "profile_id": "",
                        "device_id": "ignored",
                        "enabled": True,
                    },
                ]
            }
        )

    app = web.Application()
    app.router.add_get("/api/hermes/browser/profile-bindings", profile_bindings)
    server = await aiohttp_server(app)

    provider = WebUIBindingProvider(f"http://{server.host}:{server.port}", token="router-secret")

    assert await provider.load_bindings() == {"xuxiaofeng_profile": "span-macbook"}
    assert await provider.load_binding_policies() == {
        "xuxiaofeng_profile": {"local_action_mode": "confirm"},
    }
    assert seen_headers == ["Bearer router-secret", "Bearer router-secret"]
