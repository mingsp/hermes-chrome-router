import aiohttp

from router.internal_forwarder import InternalCommandForwarder
from router.registry import TransitCommand


class FakeResponse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.requests = []

    def post(self, url, *, json, headers, timeout):
        self.requests.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse()


async def test_internal_forwarder_sets_request_timeout_from_command_timeout():
    session = FakeSession()
    forwarder = InternalCommandForwarder(
        {"router-b": "http://router-b.internal:8787/"},
        token="dev-token",
        session=session,
    )

    await forwarder.forward_command(
        "router-b",
        TransitCommand(
            id="cmd_remote",
            profile_id="xuxiaofeng_profile",
            cloud_bridge_url="http://cloud",
            action="page.click",
            params={"uid": "el-1"},
            timeout_ms=2500,
        ),
    )

    assert session.requests == [
        {
            "url": "http://router-b.internal:8787/internal/commands",
            "json": {
                "id": "cmd_remote",
                "profileId": "xuxiaofeng_profile",
                "cloudBridgeUrl": "http://cloud",
                "action": "page.click",
                "params": {"uid": "el-1"},
                "timeoutMs": 2500,
            },
            "headers": {"Authorization": "Bearer dev-token"},
            "timeout": session.requests[0]["timeout"],
        }
    ]
    assert isinstance(session.requests[0]["timeout"], aiohttp.ClientTimeout)
    assert session.requests[0]["timeout"].total == 2.5
