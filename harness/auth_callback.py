from __future__ import annotations

from aiohttp import web

from harness.auth_config import LocalClientAuthConfigStore


def _query_value(request: web.Request, key: str) -> str:
    return str(request.query.get(key) or "").strip()


def _profile_ids(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


async def _auth_callback(request: web.Request) -> web.Response:
    store: LocalClientAuthConfigStore = request.app["auth_config_store"]
    server_url = _query_value(request, "serverUrl")
    router_url = _query_value(request, "routerUrl")
    device_id = _query_value(request, "deviceId")
    token = _query_value(request, "token")
    profile_ids = _profile_ids(_query_value(request, "profileIds"))

    if not server_url or not router_url or not device_id or not token:
        return web.json_response(
            {"ok": False, "error": "missing required authorization fields"},
            status=400,
        )

    authorization = store.save_authorization(
        server_url=server_url,
        router_url=router_url,
        device_id=device_id,
        token=token,
        profile_ids=profile_ids,
    )
    return web.json_response({"ok": True, "deviceId": authorization.device_id})


def create_auth_callback_app(store: LocalClientAuthConfigStore | None = None) -> web.Application:
    app = web.Application()
    app["auth_config_store"] = store or LocalClientAuthConfigStore()
    app.router.add_get("/auth/callback", _auth_callback)
    return app
