"""機械チェック（仕様書 §9 の V01〜V13）。

validate_candidate() は (判定, 理由リスト) を返す。
  判定: "accept"（自動公開してよい）/ "hold"（保留・Issueへ）/ "discard"（形が壊れている・破棄）
V14（重複）・V15（内容変更）は daily.py の突き合わせで扱う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from campaign_shape import CANDIDATE_VALIDATOR, unknown_required_fields

from common import (
    domain_allowed,
    normalize_text,
    now_jst,
    parse_dt,
)


@dataclass
class Context:
    brands: dict            # id -> brand
    stores: dict            # id -> store
    source: dict | None     # sources.yaml の1件（承認経由のときは None でも可）
    page_text: str | None   # 取得したページ本文（承認経由の再チェックでは None）
    config: dict
    url_checker: Callable[[str, set], tuple[bool, str]] | None = None  # (ok, 理由)
    only: set = field(default_factory=set)  # 空なら全チェック。指定時はその番号だけ

    def run(self, code: str) -> bool:
        return not self.only or code in self.only


def allowed_domains(c: dict, ctx: Context) -> set[str]:
    domains: set[str] = set()
    if ctx.source and ctx.source.get("kind") == "official":
        domains.update(ctx.source.get("domains") or [])
    for b in c.get("brand_ids") or []:
        domains.update((ctx.brands.get(b) or {}).get("domains") or [])
    for s in (c.get("scope") or {}).get("store_ids") or []:
        domains.update((ctx.stores.get(s) or {}).get("domains") or [])
    return {d.lower() for d in domains}


def _num_variants(x: float) -> list[str]:
    if x is None:
        return []
    if float(x).is_integer():
        return [str(int(x))]
    s = f"{x}".rstrip("0").rstrip(".")
    return [s]


def _date_variants(dt) -> list[str]:
    m, d = dt.month, dt.day
    return [f"{m}月{d}日", f"{m}/{d}", f"{m:02d}/{d:02d}", f"{m}.{d}", f"{m:02d}.{d:02d}", f"{m}月{d}"]


def _window(page_norm: str, quote_norm: str, width: int) -> str:
    pos = page_norm.find(quote_norm)
    if pos < 0:
        return quote_norm
    return page_norm[max(0, pos - width): pos + len(quote_norm) + width]


def validate_candidate(c: dict, ctx: Context) -> tuple[str, list[str]]:
    reasons: list[str] = []
    cfg_v = ctx.config.get("validation", {})
    holds = ctx.config.get("hold_rules", {})
    now = now_jst()

    # V01 形のチェック（壊れていたら破棄）
    # 形と不明値のゲートは、承認経路や検査番号の指定でも省略しない。
    errors = sorted(CANDIDATE_VALIDATOR.iter_errors(c), key=lambda e: list(e.path))
    if errors:
        msgs = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5]]
        return "discard", ["V01 形式エラー: " + " / ".join(msgs)]
    missing = unknown_required_fields(c)
    if missing:
        # 不明な日付をparse_dtへ渡さず、falseや仮の日付を補わず保留する。
        return "hold", missing

    scope = c.get("scope") or {}
    entry = c.get("entry") or {}
    benefit = c.get("benefit") or {}

    # V02 必須項目
    if ctx.run("V02"):
        if scope.get("kind") == "stores" and not scope.get("store_ids"):
            reasons.append("V02 対象店（store_ids）が空")
        if scope.get("kind") == "region" and not scope.get("prefecture_codes"):
            reasons.append("V02 都道府県（prefecture_codes）が空")
        # エントリーがアプリ内だけのキャンペーンもあるため、エントリーURLが無いこと自体は保留にしない
        # （サイトでは公式ページへのボタンを「エントリー方法を見る」と表示する）
        if not (c.get("evidence_quote") or "").strip():
            reasons.append("V02 根拠文が空")

    # V03 マスタに存在するか
    if ctx.run("V03"):
        for b in c.get("brand_ids") or []:
            if b not in ctx.brands:
                reasons.append(f"V03 未登録のブランド: {b}")
        for s in scope.get("store_ids") or []:
            if s not in ctx.stores:
                reasons.append(f"V03 未登録の店: {s}")

    # V04 公式ドメインか
    allowed = allowed_domains(c, ctx)
    if ctx.run("V04"):
        if not domain_allowed(c.get("official_url", ""), allowed):
            reasons.append(f"V04 公式URLのドメインが公式と一致しない: {c.get('official_url')}")
        if entry.get("url") and not domain_allowed(entry["url"], allowed):
            reasons.append(f"V04 エントリーURLのドメインが公式と一致しない: {entry['url']}")

    # V05 URL が開けるか
    if ctx.run("V05") and cfg_v.get("check_urls", True) and ctx.url_checker:
        for label, u in (("公式URL", c.get("official_url")), ("エントリーURL", entry.get("url"))):
            if not u:
                continue
            ok, why = ctx.url_checker(u, allowed)
            if not ok:
                reasons.append(f"V05 {label}が開けない: {why}")

    # V06 根拠文がページに実在するか / V07 根拠の近くに数字・日付があるか
    if ctx.page_text is not None:
        page_norm = normalize_text(ctx.page_text)
        quote_norm = normalize_text(c.get("evidence_quote", ""))
        found = bool(quote_norm) and quote_norm in page_norm
        if ctx.run("V06") and not found:
            reasons.append("V06 根拠文が公式ページ本文に見つからない")
        if ctx.run("V07") and found:
            near = _window(page_norm, quote_norm, int(cfg_v.get("evidence_window", 600)))
            rate = benefit.get("rate_max")
            if rate is not None and not any(v in near for v in _num_variants(rate)):
                reasons.append(f"V07 根拠の近くに還元率 {rate} が見つからない")
            try:
                start = parse_dt(c["period"]["start"])
                end = parse_dt(c["period"]["end"])
                if not any(v in near for v in _date_variants(start) + _date_variants(end)):
                    reasons.append("V07 根拠の近くに開始日・終了日が見つからない")
            except (KeyError, ValueError):
                pass

    # V08 期間
    if ctx.run("V08"):
        try:
            start = parse_dt(c["period"]["start"])
            end = parse_dt(c["period"]["end"])
            if start > end:
                reasons.append("V08 開始が終了より後")
            if end < now - timedelta(days=1):
                reasons.append("V08 すでに終了している")
            if end > now + timedelta(days=int(cfg_v.get("max_days_ahead", 400))):
                reasons.append("V08 終了日が先すぎる")
        except (KeyError, ValueError) as e:
            reasons.append(f"V08 日時が読めない: {e}")

    # V09 還元率の範囲
    rate = benefit.get("rate_max")
    if ctx.run("V09") and rate is not None and not (0 < rate <= 100):
        reasons.append(f"V09 還元率が範囲外: {rate}")

    # V10 大きすぎる還元・上限は人が見る
    if ctx.run("V10"):
        if rate is not None and rate >= float(holds.get("rate_max_at_or_above", 20)):
            reasons.append(f"V10 還元率が大きい（{rate}%）ため確認が必要")
        cap = benefit.get("cap_total")
        if cap is not None and cap >= int(holds.get("cap_total_at_or_above", 10000)):
            reasons.append(f"V10 期間上限が大きい（{cap}）ため確認が必要")

    # V11 改悪・制度変更は人が見る
    if ctx.run("V11") and c.get("type") == "change":
        reasons.append("V11 改悪・制度変更は自動公開しない")

    # V12 未確認・二次情報は人が見る
    if ctx.run("V12"):
        if c.get("confidence") != "confirmed":
            reasons.append("V12 公式で確認できていない（unconfirmed）")
        if ctx.source and ctx.source.get("kind") == "secondary":
            reasons.append("V12 二次情報（まとめサイト）が取得元")

    # V13 文字数・禁止語
    if ctx.run("V13"):
        banned = ctx.config.get("banned_words", [])
        for key in ("title", "conditions"):
            text = c.get(key) or ""
            for w in banned:
                if w in text:
                    reasons.append(f"V13 禁止語「{w}」が {key} に含まれる")
        rt = benefit.get("rate_text") or ""
        for w in banned:
            if w in rt:
                reasons.append(f"V13 禁止語「{w}」が rate_text に含まれる")
        for key in ("title", "conditions"):
            if re.search(r"(私|わたし)(は|が|も)", c.get(key) or ""):
                reasons.append(f"V13 一人称の表現が {key} に含まれる")

    return ("hold" if reasons else "accept"), reasons


def http_url_checker(config: dict):
    """V05 用：URL が 200 で開け、最終URLが公式ドメイン内かを確かめる関数を返す。"""
    from common import http_session

    fetch = config.get("fetch", {})
    headers = {"User-Agent": fetch.get("user_agent", "PoikatsuKouryakuBot/1.0")}
    timeout = int(fetch.get("timeout_sec", 20))
    cache: dict[str, tuple[bool, str]] = {}

    def check(url: str, allowed: set) -> tuple[bool, str]:
        if url in cache:
            return cache[url]
        result = (False, "不明")
        try:
            sess = http_session(url, config)
            r = sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code in (403, 404, 405) or r.status_code >= 500:
                r = sess.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
            if r.status_code != 200:
                result = (False, f"HTTP {r.status_code}")
            elif not domain_allowed(r.url, allowed):
                result = (False, f"公式外へ転送: {r.url}")
            else:
                result = (True, "")
        except Exception as e:  # noqa: BLE001
            result = (False, f"接続エラー: {type(e).__name__}")
        cache[url] = result
        return result

    return check
