"""Offline product tests cannot open network sockets."""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("DEEPSEEK_API_KEY", "WIKI_API_KEY", "WIKI_MODEL_BASE_URL", "WIKI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    def reject(*args, **kwargs):
        raise AssertionError("Offline test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject)
