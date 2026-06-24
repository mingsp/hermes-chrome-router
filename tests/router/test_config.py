import json

from router.config import RouterConfig, load_binding_policies, load_bindings, load_config


def test_load_bindings_maps_profile_to_device(tmp_path):
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps(
            {
                "bindings": [
                    {"profileId": "xuxiaofeng_profile", "deviceId": "span-macbook"},
                    {"profileId": "default", "deviceId": "lab-mac"},
                ]
            }
        )
    )

    assert load_bindings(path) == {
        "xuxiaofeng_profile": "span-macbook",
        "default": "lab-mac",
    }


def test_load_binding_policies_maps_profile_action_policy(tmp_path):
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "profileId": "xuxiaofeng_profile",
                        "deviceId": "span-macbook",
                        "actionPolicy": {"local_action_mode": "deny"},
                    },
                    {"profileId": "default", "deviceId": "lab-mac", "actionPolicy": []},
                ]
            }
        )
    )

    assert load_binding_policies(path) == {
        "xuxiaofeng_profile": {"local_action_mode": "deny"},
    }


def test_load_config_reads_env(monkeypatch, tmp_path):
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps({"bindings": []}))
    monkeypatch.setenv("HERMES_CHROME_ROUTER_HOST", "0.0.0.0")
    monkeypatch.setenv("HERMES_CHROME_ROUTER_PORT", "9999")
    monkeypatch.setenv("HERMES_CHROME_CLOUD_BRIDGE_URL", "http://127.0.0.1:19090")
    monkeypatch.setenv("HERMES_CHROME_ROUTER_TOKEN", "secret")
    monkeypatch.setenv("HERMES_CHROME_BINDINGS_PATH", str(bindings))
    monkeypatch.setenv("HERMES_CHROME_MIN_CLIENT_VERSION", "0.2.0")
    monkeypatch.setenv("HERMES_CHROME_RATE_LIMIT_PER_USER", "7")
    monkeypatch.setenv("HERMES_CHROME_RATE_LIMIT_BURST", "11")
    monkeypatch.setenv("HERMES_CHROME_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("HERMES_CHROME_ROUTER_SERVER_ID", "router-a")
    monkeypatch.setenv("HERMES_CHROME_ROUTE_TTL_SECONDS", "45")
    monkeypatch.setenv("HERMES_CHROME_ROUTER_PEERS", '{"router-b":"http://router-b.internal:8787"}')
    monkeypatch.setenv("HERMES_CHROME_ROUTER_AUDIT_DB", str(tmp_path / "audit.sqlite3"))

    assert load_config() == RouterConfig(
        host="0.0.0.0",
        port=9999,
        cloud_bridge_url="http://127.0.0.1:19090",
        router_token="secret",
        bindings={},
        binding_policies={},
        bindings_path=bindings,
        web_ui_url="",
        min_client_version="0.2.0",
        rate_limit_per_profile=7,
        rate_limit_burst=11,
        redis_url="redis://redis:6379/0",
        server_id="router-a",
        route_ttl_seconds=45,
        peer_urls={"router-b": "http://router-b.internal:8787"},
        audit_db_path=tmp_path / "audit.sqlite3",
    )


def test_load_config_reads_web_ui_url_without_bindings_path(monkeypatch):
    monkeypatch.setenv("HERMES_CHROME_CLOUD_BRIDGE_URL", "http://127.0.0.1:19090")
    monkeypatch.setenv("HERMES_CHROME_ROUTER_TOKEN", "secret")
    monkeypatch.setenv("HERMES_CHROME_WEB_UI_URL", "http://127.0.0.1:8648/")
    monkeypatch.delenv("HERMES_CHROME_BINDINGS_PATH", raising=False)

    config = load_config()

    assert config.bindings_path is None
    assert config.bindings == {}
    assert config.web_ui_url == "http://127.0.0.1:8648"
