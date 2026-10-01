"""Hosted-CI container lifecycle acceptance; synthetic data, no model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


def pdf_fixture():
    stream = b"BT /F1 18 Tf 50 700 Td (Container persistence fixture) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("RUNNER_ENVIRONMENT") != "github-hosted":
        parser.error("Run only on a disposable GitHub-hosted runner")
    if not re.fullmatch(r"dlos-ci-[a-z0-9-]+", args.project):
        parser.error("A unique dlos-ci-* project name is required")
    env = dict(os.environ, DLOS_HOST_PORT="19877", DLOS_WIKI_PORT="19878", DLOS_PAPER_PORT="19879")
    command = ["docker", "compose", "--env-file", os.devnull, "--project-name", args.project]
    for kind in ("container", "volume"):
        found = subprocess.check_output(["docker", kind, "ls", "-q", "--filter",
            f"label=com.docker.compose.project={args.project}"], text=True)
        if found.strip():
            raise RuntimeError("Refusing to reuse an existing test project")
    opener = build_opener(ProxyHandler({}))

    def compose(*values):
        subprocess.run([*command, *values], env=env, check=True, timeout=180)

    def request(port, path, data=None, token="", method=None, headers=None, expected=200):
        head = dict(headers or {})
        if token:
            head["x-aos-token" if port == 19877 else "x-paper-token"] = token
        if isinstance(data, dict):
            head["Content-Type"] = "application/json"
            data = json.dumps(data).encode()
        call = Request(f"http://127.0.0.1:{port}{path}", data=data, headers=head, method=method)
        try:
            with opener.open(call, timeout=15) as response:
                code, raw = response.status, response.read()
        except HTTPError as exc:
            code, raw = exc.code, exc.read()
        assert code == expected, (port, path, code, expected)
        return raw

    def wait_ready():
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                for port, path in ((19877, "/api/v1/identity"), (19878, "/aos/v1/describe"), (19879, "/aos/v1/describe")):
                    request(port, path)
                return
            except (URLError, OSError, AssertionError):
                time.sleep(1)
        raise RuntimeError("Container products did not become ready")

    def host_token():
        page = request(19877, "/settings/models").decode()
        return re.search(r'name="aos-appearance"[^>]*data-token="([^"]+)"', page)[1]

    started = False
    try:
        started = True
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "100")
        wait_ready()
        container = subprocess.check_output([*command, "ps", "-q", "app"], env=env, text=True).strip()
        info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
        assert info["Config"]["User"] == "10001:10001"
        assert info["HostConfig"]["ReadonlyRootfs"]
        assert "ALL" in info["HostConfig"]["CapDrop"]
        assert all(binding["HostIp"] == "127.0.0.1"
                   for bindings in info["HostConfig"]["PortBindings"].values() for binding in bindings)
        assert all(mount["Type"] != "bind" for mount in info["Mounts"])
        identity = json.loads(request(19878, "/aos/v1/describe"))["instance_id"]
        token = host_token()
        request(19877, "/api/v1/appearance", {"mode": "dark"}, expected=403)
        request(19877, "/api/v1/appearance", {"mode": "dark"}, token,
                headers={"Origin": "https://outside.invalid"}, expected=403)
        request(19877, "/api/v1/appearance", {"mode": "dark"}, token)
        fake_key = "ci-synthetic-not-a-provider-secret"
        result = request(19877, "/api/v1/llm/connections",
                         {"name": "CI disabled fixture", "protocol": "chat",
                          "base_url": "https://example.invalid/v1", "key": fake_key, "enabled": False}, token)
        assert fake_key.encode() not in result
        connection = json.loads(result)["connections"][0]
        assert connection["configured"] and not connection["enabled"]
        paper_page = request(19879, "/").decode()
        paper_token = re.search(r'name="paper-token" content="([^"]+)"', paper_page)[1]
        raw_pdf = pdf_fixture()
        boundary = "dlos-ci-upload"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"ci-paper.pdf\"\r\n"
                "Content-Type: application/pdf\r\n\r\n").encode() + raw_pdf + f"\r\n--{boundary}--\r\n".encode()
        uploaded = json.loads(request(19879, "/api/documents", body, paper_token,
                                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, expected=201))
        doc, session = uploaded["document"], uploaded["session"]
        assert doc["id"] == hashlib.sha256(raw_pdf).hexdigest()
        assert len(doc["parsed"]["pages"]) == 1
        request(19879, f"/api/sessions/{session['id']}/position", {"page": 1, "zoom": 1.5}, paper_token, method="PUT")
        request(19879, "/api/notes", {"session": session["id"], "body": "Persistent CI note",
                "parse_sha256": doc["parsed"]["parse_sha256"]}, paper_token, expected=201)
        assert request(19879, f"/api/documents/{doc['id']}/pdf") == raw_pdf
        # Recreate the container, retaining only the project volume.
        compose("down")
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "100")
        wait_ready()
        assert json.loads(request(19877, "/api/v1/appearance"))["mode"] == "dark"
        saved = json.loads(request(19877, "/api/v1/llm", token=host_token()))
        assert any(c["id"] == connection["id"] and c["configured"] and not c["enabled"] for c in saved["connections"])
        assert fake_key not in json.dumps(saved)
        reopened = json.loads(request(19879, f"/api/sessions/{session['id']}"))
        assert reopened["session"]["zoom"] == 1.5 and reopened["notes"][0]["body"] == "Persistent CI note"
        assert request(19879, f"/api/documents/{doc['id']}/pdf") == raw_pdf
        assert json.loads(request(19878, "/aos/v1/describe"))["instance_id"] == identity
        folder = Path(".local/preparation")
        folder.mkdir(parents=True, exist_ok=True)
        report = {"status": "passed", "three_services": True, "http_pdf_upload_retrieve": True,
                  "settings_credentials_notes_position_persist_after_recreate": True,
                  "write_auth_and_origin_rejection": True, "secrets_not_in_public_response": True,
                  "model_calls": 0, "image_target": env.get("DLOS_IMAGE_TARGET", "full"),
                  "limitations": "No real model response, native desktop, or full PDF parsing quality tested here."}
        (folder / "docker-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        if started:
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
