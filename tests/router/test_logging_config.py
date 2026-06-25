import logging

from router.logging_config import parse_log_level


def test_parse_log_level_defaults_to_info():
    assert parse_log_level({}) == logging.INFO


def test_parse_log_level_uses_env_override():
    assert parse_log_level({"HERMES_CHROME_ROUTER_LOG_LEVEL": "debug"}) == logging.DEBUG


def test_parse_log_level_rejects_unknown_values():
    assert parse_log_level({"HERMES_CHROME_ROUTER_LOG_LEVEL": "verbose"}) == logging.INFO
