from aiohttp import web

from router.app import create_router_app
from router.config import load_config
from router.logging_config import configure_logging


def main() -> None:
    configure_logging()
    config = load_config()
    web.run_app(create_router_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
