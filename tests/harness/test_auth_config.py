import json
import stat

from aiohttp.test_utils import TestClient, TestServer


def test_local_client_config_store_keeps_token_out_of_config_json(tmp_path):
    from harness.auth_config import LocalClientAuthConfigStore

    store = LocalClientAuthConfigStore(tmp_path)
    store.save_authorization(
        server_url="https://studio.example.com/",
        router_url="wss://agent.example.com/bridge",
        device_id="span-macbook",
        token="hclc_secret",
        profile_ids=("xuxiaofeng_profile", "default"),
    )

    config_payload = json.loads((tmp_path / "config.json").read_text())
    assert config_payload == {
        "serverUrl": "https://studio.example.com",
        "routerUrl": "wss://agent.example.com/bridge",
        "deviceId": "span-macbook",
        "profileIds": ["xuxiaofeng_profile", "default"],
    }
    assert "hclc_secret" not in (tmp_path / "config.json").read_text()
    assert (tmp_path / "token").read_text() == "hclc_secret"
    assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600

    authorized = store.load_authorization()
    assert authorized.server_url == "https://studio.example.com"
    assert authorized.router_url == "wss://agent.example.com/bridge"
    assert authorized.device_id == "span-macbook"
    assert authorized.token == "hclc_secret"
    assert authorized.profile_ids == ("xuxiaofeng_profile", "default")


async def test_auth_callback_saves_local_client_authorization(tmp_path):
    from harness.auth_callback import create_auth_callback_app
    from harness.auth_config import LocalClientAuthConfigStore

    store = LocalClientAuthConfigStore(tmp_path)
    client = TestClient(TestServer(create_auth_callback_app(store)))
    await client.start_server()
    try:
        resp = await client.get(
            "/auth/callback",
            params={
                "serverUrl": "https://studio.example.com/",
                "routerUrl": "wss://agent.example.com/bridge",
                "deviceId": "span-macbook",
                "token": "hclc_secret",
                "profileIds": "xuxiaofeng_profile, default",
            },
        )
        assert resp.status == 200
        assert await resp.json() == {"ok": True, "deviceId": "span-macbook"}
    finally:
        await client.close()

    authorized = store.load_authorization()
    assert authorized is not None
    assert authorized.server_url == "https://studio.example.com"
    assert authorized.router_url == "wss://agent.example.com/bridge"
    assert authorized.device_id == "span-macbook"
    assert authorized.token == "hclc_secret"
    assert authorized.profile_ids == ("xuxiaofeng_profile", "default")


async def test_auth_callback_rejects_missing_required_fields(tmp_path):
    from harness.auth_callback import create_auth_callback_app
    from harness.auth_config import LocalClientAuthConfigStore

    store = LocalClientAuthConfigStore(tmp_path)
    client = TestClient(TestServer(create_auth_callback_app(store)))
    await client.start_server()
    try:
        resp = await client.get("/auth/callback", params={"deviceId": "span-macbook"})
        assert resp.status == 400
        assert await resp.json() == {"ok": False, "error": "missing required authorization fields"}
    finally:
        await client.close()

    assert store.load_authorization() is None


def test_load_config_uses_persisted_authorization_when_env_is_absent(tmp_path, monkeypatch):
    from harness.auth_config import LocalClientAuthConfigStore
    from harness.config import load_config

    for key in (
        "HERMES_CHROME_ROUTER_URL",
        "HERMES_CHROME_ROUTER_TOKEN",
        "HERMES_CHROME_DEVICE_ID",
        "HERMES_CHROME_PROFILE_IDS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HERMES_CHROME_LOCAL_CONFIG_DIR", str(tmp_path))

    LocalClientAuthConfigStore(tmp_path).save_authorization(
        server_url="https://studio.example.com",
        router_url="wss://agent.example.com/bridge",
        device_id="span-macbook",
        token="hclc_secret",
        profile_ids=("xuxiaofeng_profile",),
    )

    config = load_config()

    assert config.router_url == "wss://agent.example.com/bridge"
    assert config.router_token == "hclc_secret"
    assert config.device_id == "span-macbook"
    assert config.profile_ids == ("xuxiaofeng_profile",)
