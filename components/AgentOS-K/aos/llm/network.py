"""Explicit instance-local outbound policy; local service traffic is unaffected."""
from .store import proxy_address

CHANNELS = ("default", "models", "paper", "wiki")


def defaults():
    return {name: {"mode": "direct" if name == "default" else "inherit", "proxy": ""}
            for name in CHANNELS}


def validate(value):
    if not isinstance(value, dict) or set(value) != set(CHANNELS):
        raise ValueError("invalid_network_settings")
    result = {}
    for name, item in value.items():
        if not isinstance(item, dict) or set(item) != {"mode", "proxy"}:
            raise ValueError("invalid_network_settings")
        modes = {"direct", "proxy"} if name == "default" else {"direct", "proxy", "inherit"}
        if item["mode"] not in modes or not isinstance(item["proxy"], str):
            raise ValueError("invalid_network_settings")
        result[name] = {"mode": item["mode"], "proxy": proxy_address(item["proxy"]) if item["mode"] == "proxy" else ""}
    return result


def effective(state, channel, connection_proxy=None):
    if channel not in CHANNELS:
        raise ValueError("invalid_network_channel")
    if connection_proxy:
        return {"proxy": connection_proxy, "source": "connection"}
    settings = state.get("network", defaults())
    selected = settings[channel]
    source = channel
    if selected["mode"] == "inherit":
        selected, source = settings["default"], "default"
    return {"proxy": selected["proxy"] if selected["mode"] == "proxy" else None, "source": source}
