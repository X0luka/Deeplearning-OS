"""Product-neutral read service for the data and knowledge center."""
from urllib.parse import urlsplit

from aos.ports import Archive


class DataCenter:
    def __init__(self, archive: Archive):
        self.archive = archive

    def records(self, source="all", page=1, product=None, q=None, thread=None):
        if source not in {"all", "product_ui", "legacy_unknown"} or page < 1:
            raise ValueError("invalid_data_filter")
        return self.archive.data_page(source, page, product, q, thread)

    def detail(self, identity):
        item = self.archive.detail(identity)
        if item and item.get("source_channel") in {"product_ui", "legacy_unknown"}:
            return item
        return None


def safe_source_url(value):
    """Only navigable HTTP(S) links; stored citations remain untrusted content."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
            return value
    except ValueError:
        pass
    return None
