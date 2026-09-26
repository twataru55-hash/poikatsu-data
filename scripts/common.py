"""共通処理：パス・時刻・JSON入出力・ID・文字列正規化・状態計算。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))

CONFIG_DIR = ROOT / "config"
MASTER_DIR = ROOT / "master"
DATA_DIR = ROOT / "data"
SCHEMA_DIR = ROOT / "schema"
DIST_DIR = ROOT / "dist"
REPORTS_DIR = ROOT / "reports"
WORK_DIR = ROOT / "work"  # 取得したページ本文の一時置き場（git 管理外）


# ---------- 時刻 ----------
def now_jst() -> datetime:
    """現在時刻（JST）。テスト用に環境変数 POIKATSU_NOW で上書きできる。"""
    fixed = os.environ.get("POIKATSU_NOW")
    if fixed:
        return parse_dt(fixed)
    return datetime.now(JST)


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def today_str(now: datetime | None = None) -> str:
    return (now or now_jst()).strftime("%Y-%m-%d")


# ---------- JSON / YAML ----------
def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def load_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config() -> dict:
    return load_yaml(CONFIG_DIR / "pipeline.yaml", {}) or {}


def load_sources() -> list[dict]:
    return load_yaml(CONFIG_DIR / "sources.yaml", []) or []


def load_master() -> dict:
    return {
        "brands": load_json(MASTER_DIR / "brands.json", []),
        "stores": load_json(MASTER_DIR / "stores.json", []),
        "recurring": load_json(MASTER_DIR / "recurring.json", []),
        "phrases": load_json(MASTER_DIR / "phrases.json", {}),
        "pr_links": load_json(MASTER_DIR / "pr_links.json", []),
    }


# ---------- 文字列 ----------
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """NFKC 正規化し、空白をすべて除去する（根拠文の照合用）。"""
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def domain_of(url: str) -> str:
    m = re.match(r"^https?://([^/:?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def domain_allowed(url: str, allowed: set[str]) -> bool:
    host = domain_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in allowed)


# ---------- ID ----------
def campaign_id(c: dict) -> str:
    scope = c.get("scope") or {}
    key = json.dumps(
        [
            sorted(c.get("brand_ids") or []),
            scope.get("kind"),
            sorted(scope.get("store_ids") or []),
            sorted(scope.get("prefecture_codes") or []),
            scope.get("municipality"),
            (c.get("period") or {}).get("start"),
            c.get("title"),
        ],
        ensure_ascii=False,
    )
    return "cp-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def identity_key(c: dict) -> tuple:
    """同じキャンペーン候補かどうかの粗い判定キー（種類・ブランド・対象・開始日時・公式URL）。
    公式URL（詳細ページ）が違えば別キャンペーン。最終判定はタイトル・根拠文の近さも見る（candidates.match_existing）。"""
    scope = c.get("scope") or {}
    return (
        c.get("type"),
        tuple(sorted(c.get("brand_ids") or [])),
        scope.get("kind"),
        tuple(sorted(scope.get("store_ids") or [])),
        tuple(sorted(scope.get("prefecture_codes") or [])),
        scope.get("municipality") or None,
        (c.get("period") or {}).get("start"),
        (c.get("official_url") or "").rstrip("/"),
    )


def content_key(c: dict) -> tuple:
    """内容が同じかどうか（終了日・還元率・上限）。"""
    b = c.get("benefit") or {}
    return (
        (c.get("period") or {}).get("end"),
        b.get("rate_max"),
        b.get("cap_per_use"),
        b.get("cap_total"),
        (c.get("entry") or {}).get("required"),
    )


# ---------- 表示状態 ----------
ENDING_SOON_HOURS = 72


def campaign_status(c: dict, now: datetime | None = None) -> str:
    """upcoming / active / ending_soon / ended を現在時刻から計算する。"""
    now = now or now_jst()
    if c.get("status_override") == "ended":
        return "ended"
    period = c.get("period") or {}
    start = parse_dt(period["start"])
    end = parse_dt(period["end"])
    if end < now:
        return "ended"
    if start > now:
        return "upcoming"
    if end - now <= timedelta(hours=ENDING_SOON_HOURS):
        return "ending_soon"
    return "active"


def is_live(status: str) -> bool:
    return status in ("active", "ending_soon")


# ---------- 実行ログ ----------
def append_run_log(entry: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORTS_DIR / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_run_log() -> list[dict]:
    path = REPORTS_DIR / "runs.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ---------- 通信 ----------
def http_session(url: str, config: dict):
    """requests のセッション。古いSSL設定のサイト（fetch.legacy_ssl_domains）だけ互換モードで接続する。"""
    import ssl

    import requests
    from requests.adapters import HTTPAdapter

    sess = requests.Session()
    legacy = set((config.get("fetch") or {}).get("legacy_ssl_domains") or [])
    if legacy and domain_allowed(url, legacy):
        class _LegacyAdapter(HTTPAdapter):
            def init_poolmanager(self, *args, **kwargs):
                ctx = ssl.create_default_context()
                ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
                kwargs["ssl_context"] = ctx
                return super().init_poolmanager(*args, **kwargs)

        sess.mount("https://", _LegacyAdapter())
    return sess


def setup_utf8_stdout() -> None:
    """Windows でも日本語の出力で止まらないよう、標準出力・標準エラーを UTF-8 にする。"""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
