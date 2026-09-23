"""Small OpenAI-compatible chat client for local Qwen/vLLM/LM Studio endpoints."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def chat_json(system_prompt: str, user_prompt: str, model: str | None = None,
              base_url: str | None = None, api_key: str | None = None,
              temperature: float = 0.0, timeout: int = 180):
    """Call /v1/chat/completions and parse a JSON object from the response."""
    base_url = (base_url or os.environ.get("D2S_LLM_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
    api_key = api_key or os.environ.get("D2S_LLM_API_KEY", "ollama")
    model = model or os.environ.get("D2S_LLM_MODEL", "qwen3:8b")
    url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "response_format": {"type": "json_object"}
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"LLM request failed at {url}: {exc}") from exc
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end < start:
            raise RuntimeError(f"Model did not return a JSON object: {content[:500]}")
        return json.loads(content[start:end + 1])


def extract_from_datasheet(sample: dict, markdown: str, model: str | None = None,
                           base_url: str | None = None, api_key: str | None = None):
    """Ask the configured model to extract required fields with auditable evidence."""
    keys = list(sample["observations"])
    aliases = sample.get("aliases", {})
    required = [key for key, row in sample["observations"].items() if row.get("source") == "datasheet"]
    system = (
        "You extract semiconductor datasheet data for SPICE modeling. "
        "Use only the supplied datasheet text. Never infer an absent measured value. "
        "Return JSON only. Every direct datasheet value must include a verbatim evidence phrase, "
        "unit, min/typ/max selection, and test conditions. Use null and status=missing when absent. "
        "Required schema: {device_type: string, observations: {KEY: {value: number|null, unit: string|null, "
        "source: datasheet|missing, evidence: string|null, condition: string|null, selection: min|typ|max|not_applicable, "
        "status: found|missing}}}. Include every requested key."
    )
    user = json.dumps({
        "device": sample["device"],
        "candidate_device_type": sample["type"],
        "required_observation_keys": required,
        "parameter_aliases": aliases,
        "datasheet_markdown": markdown
    }, ensure_ascii=False)
    return chat_json(system, user, model, base_url, api_key)
