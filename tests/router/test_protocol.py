import pytest

from shared.protocol import (
    CloudCommand,
    HelloFrame,
    ResultFrame,
    ProtocolError,
    parse_cloud_next_payload,
    parse_hello_frame,
    parse_result_frame,
)


def test_parse_cloud_next_command_requires_profile_id():
    payload = {
        "type": "command",
        "command": {
            "id": "cmd_1",
            "profileId": "xuxiaofeng_profile",
            "action": "page.click",
            "params": {"uid": "el-3"},
        },
        "expectedExtensionVersion": "0.15.38",
    }

    command = parse_cloud_next_payload(payload)

    assert command == CloudCommand(
        id="cmd_1",
        profile_id="xuxiaofeng_profile",
        action="page.click",
        params={"uid": "el-3"},
        expected_extension_version="0.15.38",
    )


def test_parse_cloud_next_none_returns_none():
    assert parse_cloud_next_payload({"type": "none", "expectedExtensionVersion": "0.15.38"}) is None


def test_parse_cloud_next_command_rejects_missing_profile_id():
    payload = {
        "type": "command",
        "command": {"id": "cmd_1", "action": "page.click", "params": {}},
    }

    with pytest.raises(ProtocolError, match="profileId"):
        parse_cloud_next_payload(payload)


def test_parse_hello_frame_normalizes_profile_ids():
    frame = parse_hello_frame(
        {
            "type": "hello",
            "deviceId": "span-macbook",
            "token": "dev-token",
            "profileIds": [" xuxiaofeng_profile ", "default"],
            "clientVersion": "0.1.0",
        }
    )

    assert frame == HelloFrame(
        device_id="span-macbook",
        token="dev-token",
        profile_ids=("xuxiaofeng_profile", "default"),
        client_version="0.1.0",
    )


def test_parse_hello_frame_accepts_device_token_without_profile_ids():
    frame = parse_hello_frame(
        {
            "type": "hello",
            "deviceId": "span-macbook",
            "token": "dev-token",
            "clientVersion": "0.2.0",
        }
    )

    assert frame == HelloFrame(
        device_id="span-macbook",
        token="dev-token",
        profile_ids=(),
        client_version="0.2.0",
    )


@pytest.mark.parametrize("profile_ids", ["xuxiaofeng_profile", "", 0, False, None])
def test_parse_hello_frame_rejects_non_list_profile_ids(profile_ids):
    with pytest.raises(ProtocolError, match="profileIds must be a list"):
        parse_hello_frame(
            {
                "type": "hello",
                "deviceId": "span-macbook",
                "token": "dev-token",
                "profileIds": profile_ids,
                "clientVersion": "0.1.0",
            }
        )


def test_parse_result_frame_requires_matching_shape():
    frame = parse_result_frame(
        {
            "type": "result",
            "id": "cmd_1",
            "profileId": "xuxiaofeng_profile",
            "ok": False,
            "error": "extension not polling",
        }
    )

    assert frame == ResultFrame(
        id="cmd_1",
        profile_id="xuxiaofeng_profile",
        ok=False,
        result=None,
        error="extension not polling",
    )
