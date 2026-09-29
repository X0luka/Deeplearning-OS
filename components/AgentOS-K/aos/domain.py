"""Framework-free product identities and application errors."""
from dataclasses import dataclass


class ProductError(Exception):
    """A stable error code safe to display without source secrets."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    api_url: str
    ui_url: str
    navigation_label: str = ""
    navigation_icon: str = "app"
