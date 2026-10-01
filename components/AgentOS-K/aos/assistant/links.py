"""Resolve saved local citations for the current instance without rewriting history."""
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def present_turn(turn, providers):
    evidence = []
    for item in turn.get("evidence", []):
        shown = dict(item)
        target = providers.get(item.get("provider"), {}).get("url")
        try:
            old, current = urlsplit(item.get("url") or ""), urlsplit(target or "")
            if (old.scheme in {"http", "https"} and current.scheme in {"http", "https"}
                    and old.username is None and old.password is None
                    and current.username is None and current.password is None
                    and loopback(old.hostname or "") and loopback(current.hostname or "")):
                shown["url"] = urlunsplit((current.scheme, current.netloc,
                                           old.path, old.query, old.fragment))
        except ValueError:
            pass  # Malformed legacy links remain historical evidence, not routing input.
        evidence.append(shown)
    return {**turn, "evidence": evidence}
