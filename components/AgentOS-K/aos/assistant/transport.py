import json
import re
from urllib.parse import quote, urlsplit

import httpx


def finish_error(reason):
    return "model_finish_" + re.sub("[^a-z0-9_]", "", str(reason or "missing").lower())[:60]


def thinking_config(model, mode):
    if mode in {"default", "auto"}:
        return {}
    if mode == "off" and not model.startswith(("gemini-3", "gemini-2.5-pro")):
        return {"thinkingBudget": 0}
    raise ValueError("thinking_level_not_supported")


def model_request(settings, messages, tools=None, structured=False, stream=False):
    native = settings["protocol"] == "gemini"
    base, model = settings["base_url"], settings["model"]
    custom = settings.get("custom", "")
    messages = [dict(m) for m in messages]
    if custom:
        # User style preferences never replace the product's task contract.
        instruction = "\n用户回答偏好（仅在符合上述任务格式与引用要求时应用）：\n" + custom
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
    if native:
        contents, system = [], []
        for m in messages:
            role = m["role"]
            if role == "system":
                system.append({"text": m["content"]})
                continue
            if role == "tool":
                parts = [
                    {
                        "functionResponse": {
                            "name": m.get("name", m["tool_call_id"]),
                            "response": {"result": m["content"]},
                            **({"id": m["_native_call_id"]} if m.get("_native_call_id") else {}),
                        }
                    }
                ]
            elif m.get("_native_parts"):
                parts = m["_native_parts"]
            else:
                parts = [{"text": m.get("content") or ""}]
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        config = {"maxOutputTokens": settings["max_output_tokens"]}
        if settings.get("temperature") is not None:
            config["temperature"] = settings["temperature"]
        thinking = thinking_config(model, settings.get("thinking", "default"))
        if thinking:
            config["thinkingConfig"] = thinking
        if structured and settings["capabilities"].get("json") is True:
            config["responseMimeType"] = "application/json"
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": system},
            "generationConfig": config,
        }
        if settings.get("web_search"):
            if tools:
                raise ValueError("search_tools_combination_unsupported")
            payload["tools"] = [{"google_search": {}}]
        elif tools:
            payload["tools"] = [{"functionDeclarations": [t["function"] for t in tools]}]
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return (
            base + "/models/" + quote(model, safe="") + ":" + method,
            {"x-goog-api-key": settings["api_key"]},
            payload,
        )
    if settings.get("web_search") or settings.get("document_input") == "pdf":
        raise ValueError("native_feature_not_adapted")
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": settings["max_output_tokens"],
        "stream": stream,
    }
    if settings.get("temperature") is not None:
        payload["temperature"] = settings["temperature"]
    if tools:
        payload["tools"] = tools
    if structured and settings["capabilities"].get("json") is True:
        payload["response_format"] = {"type": "json_object"}
    thinking = settings.get("thinking", "default")
    if thinking != "default":
        hostname = urlsplit(base).hostname
        if hostname == "api.deepseek.com":
            payload["thinking"] = {"type": "disabled" if thinking == "off" else "enabled"}
        elif hostname == "openrouter.ai":
            payload["reasoning"] = {"enabled": thinking != "off"}
        else:
            raise ValueError("thinking_parameter_not_adapted")
    return base + "/chat/completions", {"Authorization": "Bearer " + settings["api_key"]}, payload


async def stream(settings, messages, tools, emit, transport=None):
    """Return bounded, non-secret error codes without replaying paid requests."""
    try:
        return await _stream(settings, messages, tools, emit, transport)
    except httpx.TimeoutException:
        raise ValueError("model_timeout") from None
    except httpx.ProxyError:
        raise ValueError("model_proxy_error") from None
    except httpx.ConnectError:
        raise ValueError("model_connect_error") from None
    except httpx.HTTPError:
        raise ValueError("model_network_error") from None


async def _stream(settings, messages, tools, emit, transport=None):
    url, headers, payload = model_request(settings, messages, tools or None, stream=True)
    if settings["protocol"] != "gemini":
        payload["stream_options"] = {"include_usage": True}
    answer, calls, usage, native_parts, finish = "", {}, {}, [], None
    reasoning = []
    size = 0
    # SSE metadata repeats per token; wire bytes are not answer tokens.
    wire_limit = min(256_000_000, max(2_000_000, settings["max_output_tokens"] * 512))
    async with (
        httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15),
            trust_env=False,
            proxy=settings.get("proxy"),
            transport=transport,
        ) as client,
        client.stream("POST", url, headers=headers, json=payload) as response,
    ):
        if response.status_code != 200:
            raise ValueError("provider_http_" + str(response.status_code))
        async for line in response.aiter_lines():
            size += len(line.encode("utf-8"))
            if size > wire_limit:
                raise ValueError("model_response_too_large")
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError("model_invalid_response") from None
            if settings["protocol"] == "gemini":
                usage = data.get("usageMetadata", usage)
                candidates = data.get("candidates", [])
                if not candidates:
                    continue
                candidate = candidates[0]
                finish = candidate.get("finishReason", finish)
                parts = candidate.get("content", {}).get("parts", [])
                native_parts.extend(parts)
                for part in parts:
                    if part.get("text") and not part.get("thought"):
                        answer += part["text"]
                        await emit(answer)
                    if "functionCall" in part:
                        c = part["functionCall"]
                        i = len(calls)
                        calls[i] = {
                            "id": c.get("id", str(i)),
                            "_native_id": c.get("id"),
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c.get("args", {})),
                            },
                        }
            else:
                usage = data.get("usage") or usage
                if not data.get("choices"):
                    continue
                choice = data["choices"][0]
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta", {})
                if tools and delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
                if delta.get("content"):
                    answer += delta["content"]
                    await emit(answer)
                for c in delta.get("tool_calls", []):
                    item = calls.setdefault(
                        c["index"],
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if c.get("id"):
                        item["id"] = c["id"]
                    for k in ("name", "arguments"):
                        item["function"][k] += c.get("function", {}).get(k, "")
    if finish not in ("STOP", "stop", "tool_calls"):
        raise ValueError(finish_error(finish))
    message = {"role": "assistant", "content": answer}
    if calls:
        message["tool_calls"] = list(calls.values())
    if calls and reasoning:
        # DeepSeek thinking/tool continuation requires this protocol field.
        # It stays in the transient message, never in the displayed answer.
        message["reasoning_content"] = "".join(reasoning)
    if native_parts:
        message["_native_parts"] = native_parts
    return message, usage
