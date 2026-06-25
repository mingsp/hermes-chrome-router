from __future__ import annotations

import logging
import os
from collections.abc import Mapping


LOG_LEVEL_ENV = "HERMES_CHROME_ROUTER_LOG_LEVEL"
DEFAULT_LOG_LEVEL = logging.INFO


def parse_log_level(environ: Mapping[str, str] | None = None) -> int:
    values = environ if environ is not None else os.environ
    raw_level = str(values.get(LOG_LEVEL_ENV, "")).strip().upper()
    if not raw_level:
        return DEFAULT_LOG_LEVEL
    level = logging.getLevelName(raw_level)
    if isinstance(level, int):
        return level
    return DEFAULT_LOG_LEVEL


def configure_logging(environ: Mapping[str, str] | None = None) -> int:
    level = parse_log_level(environ)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(level)
    return level
