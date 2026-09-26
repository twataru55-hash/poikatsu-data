"""安価モデルでページ本文からキャンペーン項目を抜き出す（仕様書 §8）。

モデルには文章を書かせない。決まったJSONの項目を埋めるだけ。
プロバイダは config/pipeline.yaml の llm.provider（openai / anthropic）で切り替える。
APIキーは環境変数 OPENAI_API_KEY / ANTHROPIC_API_KEY（GitHub Secrets）から読む。
"""
from __future__ import annotations

import json
import os

import requests

from common import ROOT, SCHEMA_DIR, load_json, today_str

PROMPT_PATH = ROOT / "prompts" / "extract.md"


def _output_schema() -> dict:
    s = load_json(SCHEMA_DIR / "llm_output.schema.json")
    s = {k: v for k, v in s.items() if k not in ("$schema", "title")}
    return s


def build_prompt(source: dict, page_text: str, stores: list[dict], config: dict) -> str:
    max_chars = int(config.get("llm", {}).get("max_input_chars", 30000))
    allowed_store_ids = source.get("store_ids") or [s["id"] for s in stores]
    store_list = ", ".join(
        f"{s['id']}（{s['name']}）" for s in stores if s["id"] in set(allowed_store_ids)
    ) or "（なし）"
    tpl = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        tpl.replace("{{today}}", today_str())
        .replace("{{source_name}}", source.get("name", ""))
        .replace("{{source_url}}", source.get("url", ""))
        .replace("{{allowed_brand_ids}}", ", ".join(source.get("brand_ids") or []))
        .replace("{{allowed_store_ids}}", store_list)
        .replace("{{page_text}}", page_text[:max_chars])
    )


def _call_openai(prompt: str, llm: dict) -> dict:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY が設定されていない")
    body = {
        "model": llm["model"],
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "campaigns", "strict": True, "schema": _output_schema()},
        },
        "max_completion_tokens": int(llm.get("max_output_tokens", 8192)),
    }
    if llm.get("temperature") is not None:
        body["temperature"] = llm["temperature"]
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: {r.text[:200]}")
    content = r.json()["choices"][0]["message"].get("content") or "{}"
    return json.loads(content)


def _call_anthropic(prompt: str, llm: dict) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていない")
    body = {
        "model": llm["model"],
        "max_tokens": int(llm.get("max_output_tokens", 8192)),
        "tools": [{
            "name": "record_campaigns",
            "description": "ページから書き写したキャンペーンを記録する",
            "input_schema": _output_schema(),
        }],
        "tool_choice": {"type": "tool", "name": "record_campaigns"},
        "messages": [{"role": "user", "content": prompt}],
    }
    if llm.get("temperature") is not None:
        body["temperature"] = llm["temperature"]
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=body,
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Anthropic HTTP {r.status_code}: {r.text[:200]}")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input") or {"campaigns": []}
    return {"campaigns": []}


def extract(source: dict, page_text: str, stores: list[dict], config: dict) -> list[dict]:
    """1ページ分を抽出して、候補（モデル出力そのまま）のリストを返す。"""
    llm = config.get("llm", {})
    prompt = build_prompt(source, page_text, stores, config)
    provider = llm.get("provider", "openai")
    if provider == "openai":
        out = _call_openai(prompt, llm)
    elif provider == "anthropic":
        out = _call_anthropic(prompt, llm)
    else:
        raise RuntimeError(f"未対応のプロバイダ: {provider}")
    items = out.get("campaigns") if isinstance(out, dict) else None
    return items if isinstance(items, list) else []
