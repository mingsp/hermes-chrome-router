class ProtocolError(ValueError):
    """A received frame or cloud bridge payload does not match the MVP protocol."""


class DeliveryError(RuntimeError):
    """A command could not be delivered or completed."""


def binding_not_found(profile_id: str) -> str:
    return f"browser binding not found for profile {profile_id}"


def bridge_not_connected(profile_id: str) -> str:
    return f"browser bridge not connected for profile {profile_id}"


def profile_mismatch(command_id: str, expected: str, actual: str) -> str:
    return (
        f"result profileId mismatch for command {command_id}: "
        f"expected {expected}, got {actual}"
    )
