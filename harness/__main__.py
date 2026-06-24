import asyncio

from aiohttp import web

from harness.bridge import LocalBridge
from harness.config import load_config
from harness.ws_client import RouterWebSocketClient


async def _main_async() -> None:
    config = load_config()
    ws_client = RouterWebSocketClient(
        router_url=config.router_url,
        token=config.router_token,
        device_id=config.device_id,
        profile_ids=config.profile_ids,
        local_bridge=None,
    )
    bridge = LocalBridge(result_sink=ws_client)
    ws_client.local_bridge = bridge
    runner = web.AppRunner(bridge.create_app())
    await runner.setup()
    site = web.TCPSite(runner, config.local_host, config.local_port)
    await site.start()
    try:
        await ws_client.run_forever()
    finally:
        await runner.cleanup()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
