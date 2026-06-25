import logging
from types import SimpleNamespace

import router.logging_config as logging_config
from router.logging_config import LOG_LEVEL_ENV, configure_logging, parse_log_level


def test_parse_log_level_defaults_to_info():
    assert parse_log_level({}) == logging.INFO


def test_parse_log_level_uses_env_override():
    assert parse_log_level({LOG_LEVEL_ENV: "debug"}) == logging.DEBUG


def test_parse_log_level_rejects_unknown_values():
    assert parse_log_level({LOG_LEVEL_ENV: "verbose"}) == logging.INFO


def test_configure_logging_sets_root_and_aiohttp_access_loggers(monkeypatch):
    calls = []

    def fake_basic_config(**kwargs):
        calls.append(("basicConfig", kwargs))

    access_logger = SimpleNamespace(
        setLevel=lambda level: calls.append(("aiohttp.access.setLevel", level))
    )

    def fake_get_logger(name):
        calls.append(("getLogger", name))
        return access_logger

    monkeypatch.setattr(
        logging_config,
        "logging",
        SimpleNamespace(
            getLevelName=logging.getLevelName,
            basicConfig=fake_basic_config,
            getLogger=fake_get_logger,
        ),
    )

    level = configure_logging({LOG_LEVEL_ENV: "debug"})

    assert level == logging.DEBUG
    assert calls == [
        (
            "basicConfig",
            {
                "level": logging.DEBUG,
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            },
        ),
        ("getLogger", "aiohttp.access"),
        ("aiohttp.access.setLevel", logging.DEBUG),
    ]


def test_main_configures_logging_before_loading_config_and_running_app(monkeypatch):
    import router.__main__ as router_main

    calls = []
    config = SimpleNamespace(host="127.0.0.1", port=8787)
    app = object()

    def fake_configure_logging():
        calls.append("configure_logging")

    def fake_load_config():
        calls.append("load_config")
        return config

    def fake_create_router_app(actual_config):
        calls.append(("create_router_app", actual_config))
        return app

    def fake_run_app(actual_app, *, host, port):
        calls.append(("run_app", actual_app, host, port))

    monkeypatch.setattr(router_main, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(router_main, "load_config", fake_load_config)
    monkeypatch.setattr(router_main, "create_router_app", fake_create_router_app)
    monkeypatch.setattr(router_main.web, "run_app", fake_run_app)

    router_main.main()

    assert calls == [
        "configure_logging",
        "load_config",
        ("create_router_app", config),
        ("run_app", app, "127.0.0.1", 8787),
    ]
