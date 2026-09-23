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
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    }
    if os.environ.get("D2S_LLM_RESPONSE_FORMAT", "json_object").lower() != "off":
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"LLM HTTP {exc.code} at {url}: {detail}") from exc
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


def _headings(markdown: str):
    import re
    matches = list(re.finditer(r"(?m)^#{1,4}\s+(.+?)\s*$", markdown))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        out.append({"title": match.group(1), "text": markdown[match.start():end]})
    return out or [{"title": "full document", "text": markdown}]


def run_paper_stages(sample: dict, corpus: list[dict], markdown: str,
                     model: str | None = None, base_url: str | None = None,
                     api_key: str | None = None):
    """Run LLM-backed AGDF, HDER, HNEN, classification, and extraction stages.

    The document lock acts as the human selection step from AGDF. Only the
    locked document's Markdown is passed to later stages.
    """
    def ask(stage, prompt, temp=0.0):
        data = chat_json(
            f"You are the {stage} stage in a datasheet-to-SPICE research workflow. "
            "Follow the requested JSON schema. Use only supplied evidence; do not invent facts.",
            json.dumps(prompt, ensure_ascii=False), model, base_url, api_key, temperature=temp
        )
        return data

    candidates = [{"device": item["device"], "type": item["type"], "document": item["markdown"]} for item in corpus]
    agdf_model = ask("AGDF candidate retrieval", {
        "task": "Rank candidate datasheets for the requested part and target parameters.",
        "query": {"device": sample["device"], "parameters": sample["queries"]},
        "candidates": candidates,
        "schema": {"ranked_candidates": [{"device": "", "reason": ""}]}
    })
    # Paper AGDF requires user selection after showing the candidate set.
    agdf = {"paper_method": "AGDF: global candidate search followed by user-selected document lock.",
            "query": sample["device"], "model_ranked_candidates": agdf_model.get("ranked_candidates", []),
            "selection_policy": "configured target represents the user selection after candidate display",
            "locked_document": sample["markdown"]}

    sections = _headings(markdown)
    hder_model = ask("HDER section planning", {
        "task": "Predict the primary and supplementary datasheet sections for retrieving the requested device parameters.",
        "device": sample["device"], "parameters": sample["queries"],
        "available_sections": [s["title"] for s in sections],
        "schema": {"primary_sections": [""], "supplementary_sections": [""], "reasoning_summary": ""}
    })
    primary = hder_model.get("primary_sections", [])
    supplementary = hder_model.get("supplementary_sections", [])
    chosen = [s for s in sections if any(name.lower() in s["title"].lower() or s["title"].lower() in name.lower() for name in primary + supplementary)]
    section_text = "\n\n".join(s["text"] for s in chosen) if chosen else markdown
    hder = {"paper_method": "HDER: infer and prioritize parameter-rich primary/supplementary sections.",
            "primary_prediction": primary, "supplementary_prediction": supplementary,
            "retrieval_limit": 8, "selected_section_titles": [s["title"] for s in chosen[:8]],
            "fallback_to_full_locked_document": not bool(chosen), "model_output": hder_model}

    hnen_model = ask("HNEN terminology normalization", {
        "task": "Map query and template names to terms actually used in this locked datasheet; do not change numbers.",
        "device": sample["device"], "aliases": sample.get("aliases", {}),
        "queries": sample["queries"], "locked_document_excerpt": section_text[:18000],
        "schema": {"normalized_device": "", "normalized_parameters": {"canonical": "datasheet term"}, "retry_terms": [""]}
    })
    hnen = {"paper_method": "HNEN: normalize heterogeneous model/parameter terminology and retry retrieval.",
            "implementation": "LLM normalization with aliases constrained to the locked document",
            "normalization": hnen_model}

    classification = ask("SPICE device classification", {
        "task": "Classify the described semiconductor into exactly one allowed class.",
        "allowed_classes": ["Diode", "MOSFET", "JFET", "BJT", "Others"],
        "device": sample["device"], "source_excerpt": section_text[:10000],
        "schema": {"device_type": "one allowed class", "evidence": "short evidence"}
    })
    selected_type = classification.get("device_type", "").strip()
    type_templates = {
        "Diode": ".model D1 D(IS CJO VJ M)",
        "BJT": ".model Q1 NPN(BF TF CJC CJE)",
        "MOSFET": ".model M1 NMOS(VTO CGSO CGDO KP)",
        "JFET": ".model J1 NJF(VTO CGS CGD BETA)"
    }
    if selected_type not in type_templates:
        raise ValueError(f"LLM selected unsupported device class: {selected_type}")
    if selected_type != sample["type"]:
        raise ValueError(f"LLM classified {sample['device']} as {selected_type}, while the configured sample type is {sample['type']}")

    required = [key for key, row in sample["observations"].items() if row.get("source") == "datasheet"]
    extraction = ask("HDER parameter extraction", {
        "task": "Extract values from the selected sections. Return each required key, preserve test conditions and min/typ/max. Evidence must be verbatim. Return a numeric value and its unit exactly as read; the caller converts units to SI.",
        "device": sample["device"], "device_type": selected_type,
        "template": type_templates[selected_type], "required_observation_keys": required,
        "aliases": sample.get("aliases", {}), "selected_sections": section_text[:24000],
        "schema": {"device_type": selected_type, "observations": {key: {
            "value": "number or null", "unit": "string", "source": "datasheet or missing",
            "evidence": "verbatim phrase or null", "condition": "string or null",
            "selection": "min/typ/max/not_applicable", "status": "found/missing"
        } for key in required}}
    })
    return {
        "agdf": agdf,
        "hder": hder,
        "hnen": hnen,
        "classification": {"device_type": selected_type, "template": type_templates[selected_type], "model_output": classification},
        "extraction": extraction
    }
