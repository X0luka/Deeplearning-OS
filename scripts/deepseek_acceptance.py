"""Fixed-sample paid acceptance runner. Execute only after user cost authorization."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/AgentOS-K"))
SAMPLES = {
    1: "Reply with exactly: API_TEST_OK",
    2: "用一句简短中文说明：一加一等于二。",
    3: "Reply with exactly: CONFIG_RELOAD_OK",
}


async def attempt(settings, case, transport=None):
    import httpx
    from aos.assistant.transport import model_request, stream
    if (settings["base_url"] != "https://api.deepseek.com"
            or settings["protocol"] != "chat" or settings["model"] != "deepseek-flash"
            or settings.get("proxy") not in {None, "http://127.0.0.1:7897"} or settings.get("custom")
            or settings["max_output_tokens"] != 64 or settings["thinking"] != "off"
            or settings["tools"] or settings["web_search"] or settings["document_input"] != "text"):
        raise ValueError("acceptance_configuration_changed")
    messages = [{"role": "user", "content": SAMPLES[case]}]
    started = time.monotonic()
    if case == 2:
        chunks = []
        async def emit(text):
            chunks.append(len(text))
        message, usage = await asyncio.wait_for(
            stream(settings, messages, [], emit, transport=transport), 45)
        content = message.get("content", "")
        if message.get("tool_calls") or not content:
            raise ValueError("unexpected_response")
        result = {"chunks": len(chunks), "usage": usage, "answer_chars": len(content)}
    else:
        url, headers, payload = model_request(settings, messages, stream=False)
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False,
                                     transport=transport, proxy=settings.get("proxy")) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise ValueError("provider_http_" + str(response.status_code))
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"].get("content", "")
        expected = "API_TEST_OK" if case == 1 else "CONFIG_RELOAD_OK"
        if choice.get("finish_reason") != "stop" or content.strip() != expected:
            raise ValueError("unexpected_response")
        result = {"usage": data.get("usage", {}), "answer_chars": len(content)}
    # Only numeric usage leaves the runner; no headers, keys or returned text.
    result["usage"] = {k: v for k, v in result["usage"].items() if type(v) is int}
    return {"case": case, "status": "passed", "seconds": round(time.monotonic()-started, 3), **result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--authorized-paid-test", action="store_true")
    parser.add_argument("--diagnosed-proxy-path", action="store_true")
    args = parser.parse_args()
    if not args.authorized_paid_test:
        parser.error("Explicit user authorization required; no request made")
    from aos.desktop_instance import plan, inside
    from aos.instance_guard import instance_lock
    from aos.llm.store import ModelStore
    value = plan(ROOT, "dev-api-settings", (18777, 18778, 18779))
    os.umask(0o077)
    # Settings UI must have stopped before accepting a fixed configuration.
    with instance_lock(ROOT, value.instance):
        receipt = inside(ROOT, ".local", "preparation", "deepseek-real-acceptance.json")
        record = json.loads(receipt.read_text()) if receipt.exists() else {"attempts": []}
        previous = record["attempts"]
        def accepted_prior(a):
            return a["status"] == "passed" or (
                args.diagnosed_proxy_path and a["case"] == 1
                and a["status"] == "failed" and a.get("error") == "ConnectError")
        if len(previous) != args.case - 1 or not all(accepted_prior(a) for a in previous):
            raise SystemExit("Test order or previous failure blocks requests; no automatic retry")
        settings = ModelStore(value.path("workbench") / "llm").resolve("assistant", "chat", include_secret=True)
        if args.diagnosed_proxy_path:
            settings["proxy"] = "http://127.0.0.1:7897"
        entry = {"case": args.case, "status": "attempt_reserved",
                 "network": "explicit_existing_proxy" if args.diagnosed_proxy_path else "no_explicit_proxy"}
        previous.append(entry)
        receipt.write_text(json.dumps(record, indent=2))
        try:
            result = asyncio.run(attempt(settings, args.case))
        except Exception as exc:
            safe = str(exc)
            code = safe if isinstance(exc, ValueError) and safe.replace("_", "").isalnum() and len(safe) < 80 else type(exc).__name__
            result = {"case": args.case, "status": "failed", "error": code}
        entry.update(result)
        receipt.write_text(json.dumps(record, indent=2))
        print(json.dumps(result), flush=True)
        if result["status"] != "passed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
