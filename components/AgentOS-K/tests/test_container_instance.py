"""Container opt-in must not change ordinary desktop binding or data isolation."""
import asyncio

from aos.bootstrap import parse_args
from aos.container_instance import configuration


def test_container_ports_are_explicit_and_data_remains_instance_local(monkeypatch):
    monkeypatch.setenv("DLOS_HOST_PORT", "19877")
    monkeypatch.setenv("DLOS_WIKI_PORT", "19878")
    monkeypatch.setenv("DLOS_PAPER_PORT", "19879")
    value = configuration()
    assert value.ports == (19877, 19878, 19879)
    assert value.instance == "dev-docker"
    assert value.path("workbench").is_relative_to(value.root / ".local/instances/dev-docker")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-inherited-key")
    assert "DEEPSEEK_API_KEY" not in value.environment()


def test_bind_defaults_stay_loopback_and_container_is_explicit(tmp_path):
    async def run():
        for selected in ("127.0.0.1", "0.0.0.0"):
            options = ["serve", "--products", "wiki,paper", "--data-dir", str(tmp_path)]
            if selected != "127.0.0.1":
                options += ["--listen-host", selected]
            args, providers = parse_args(options)
            assert args.listen_host == selected
            for provider in providers:
                entry = provider.create(args)
                try:
                    command = entry.services[0].command
                    assert command[command.index("--host") + 1] == selected
                    assert entry.product.api_url.startswith("http://127.0.0.1:")
                finally:
                    await entry.client.aclose()
    asyncio.run(run())
