"""User-authorized temporary loopback settings server, never model generation."""
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/AgentOS-K"))


def main():
    if os.environ.get("DLOS_ISOLATED_TEST") != "1":
        raise SystemExit("Use scripts/run-api-settings.sh")
    from aos.desktop_instance import plan
    from aos.instance_guard import instance_lock
    from api_settings import create_settings_app, deny_outbound
    value = plan(ROOT, "dev-api-settings", (18777, 18778, 18779))
    os.umask(0o077)
    with instance_lock(ROOT, value.instance):
        value.path("workbench").mkdir(parents=True, exist_ok=True, mode=0o700)
        sys.addaudithook(deny_outbound)
        import uvicorn
        app = create_settings_app(value.path("workbench"))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 18777))
            print(json.dumps({"settings_url": "http://127.0.0.1:18777/settings/models",
                              "mode": "configuration_only", "provider_calls": False}), flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False)).run(sockets=[listener])


if __name__ == "__main__":
    main()
