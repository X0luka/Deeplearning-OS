"""Discover only locally shipped extension packages; selection is a CLI concern."""
from dataclasses import dataclass, field
from importlib import import_module
from pkgutil import iter_modules
from urllib.parse import urlsplit

import httpx

from aos.adapters.process import LocalProcesses, Service
from aos.domain import Product
from aos.ports import ProductAdapter


@dataclass
class Integration:
    product: Product
    client: httpx.AsyncClient
    adapter: ProductAdapter
    services: list[Service] = field(default_factory=list)
    supervisor: type[LocalProcesses] = LocalProcesses
    manual_activation: bool = False
    autostart: bool = False
    preferred: bool = False
    startup_errors: dict[str, str] = field(default_factory=dict)
    model_tasks: dict[str, str] = field(default_factory=dict)
    assistant_section: str = ""
    network_channel: str = ""


def local_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Only local HTTP products are enabled")
    return value


def discover(selection):
    available = {m.name for m in iter_modules(__path__) if m.ispkg and not m.name.startswith('_')}
    names = sorted(available) if selection == "all" else ([] if selection == "none" else selection.split(','))
    if len(set(names)) != len(names) or any(n not in available for n in names):
        raise ValueError("Unknown or duplicate product extension")
    return [import_module(f"{__name__}.{name}") for name in names]
