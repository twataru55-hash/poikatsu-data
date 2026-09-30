"""モデル出力を campaigns.json の形に整え、既存データと突き合わせる（V14・V15）。"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

from common import JST, campaign_id, content_key, identity_key, normalize_text, today_str

SIMILARITY = 0.75  # タイトルか根拠文がこれ以上似ていれば同じキャンペーンとみなす（短いタイトルの取り違えを防ぐため高め）

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm_dt(value, is_end: bool):
    if not value or not isinstance(value, str):
        return value
    v = value.strip()
    if _DATE_ONLY.match(v):
        return v + ("T23:59:59+09:00" if is_end else "T00:00:00+09:00")
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return v
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST).isoformat(timespec="seconds")


def _clean_str(v):
    return v.strip() if isinstance(v, str) else v


def normalize(raw: dict, source: dict) -> dict:
    """モデル出力1件を保存用の形にする。欠けた値は安全側の既定値で埋める。"""
    benefit = raw.get("benefit") or {}
    scope = raw.get("scope") or {}
    period = raw.get("period") or {}
    entry = raw.get("entry") or {}
    prefs = []
    for p in scope.get("prefecture_codes") or []:
        p = str(p).strip()
        if p.isdigit():
            prefs.append(p.zfill(2))
    title = _clean_str(raw.get("title")) or ""
    c = {
        "type": raw.get("type"),
        "brand_ids": sorted({b.strip() for b in raw.get("brand_ids") or [] if isinstance(b, str) and b.strip()}),
        "title": title[:40],
        "benefit": {
            "rate_max": benefit.get("rate_max"),
            "rate_text": _clean_str(benefit.get("rate_text")) or "",
            "cap_per_use": benefit.get("cap_per_use"),
            "cap_total": benefit.get("cap_total"),
            "cap_unit": benefit.get("cap_unit"),
        },
        "scope": {
            "kind": scope.get("kind"),
            "store_ids": sorted({s for s in scope.get("store_ids") or [] if isinstance(s, str) and s}),
            "prefecture_codes": sorted(set(prefs)),
            "municipality": _clean_str(scope.get("municipality")) or None,
        },
        "period": {"start": _norm_dt(period.get("start"), False), "end": _norm_dt(period.get("end"), True)},
        "entry": {"required": entry.get("required"), "url": _clean_str(entry.get("url")) or None},
        "conditions": _clean_str(raw.get("conditions")) or None,
        "official_url": _clean_str(raw.get("official_url")) or source.get("url"),
        "evidence_quote": _clean_str(raw.get("evidence_quote")) or "",
        "confidence": raw.get("confidence") if raw.get("confidence") in ("confirmed", "unconfirmed") else "unconfirmed",
        "source_id": source["id"],
        "first_seen": today_str(),
        "last_verified": today_str(),
        "status_override": None,
    }
    if source.get("kind") == "secondary":
        c["confidence"] = "unconfirmed"
    c["id"] = campaign_id(c)
    return c


def match_existing(c: dict, existing: list[dict]) -> tuple[str, dict | None]:
    """既存と突き合わせる。
    戻り値: ("new", None) / ("same", 既存) / ("changed", 既存)
    """
    ik = identity_key(c)
    best, best_score = None, 0.0
    for e in existing:
        if identity_key(e) != ik:
            continue
        score = max(
            SequenceMatcher(None, normalize_text(e.get("title", "")), normalize_text(c.get("title", ""))).ratio(),
            SequenceMatcher(None, normalize_text(e.get("evidence_quote", "")), normalize_text(c.get("evidence_quote", ""))).ratio(),
        )
        if score > best_score:
            best, best_score = e, score
    if best is None or best_score < SIMILARITY:
        return "new", None
    if content_key(best) == content_key(c):
        return "same", best
    return "changed", best


def apply_update(existing: dict, new: dict) -> dict:
    """V15：内容が変わった既存キャンペーンを更新する（ID と first_seen は維持）。"""
    updated = dict(new)
    updated["id"] = existing["id"]
    updated["first_seen"] = existing.get("first_seen", new["first_seen"])
    return updated
