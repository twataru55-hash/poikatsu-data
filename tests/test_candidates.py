"""モデル出力の整形と、既存との突き合わせ（V14・V15）。"""
from candidates import apply_update, match_existing, normalize

SRC = {"id": "src-a", "url": "https://paypay.ne.jp/event/", "kind": "official"}
RAW = {
    "type": "reward", "brand_ids": ["paypay"], "title": " 最大10%戻ってくる ",
    "benefit": {"rate_max": 10, "rate_text": "最大10%", "cap_per_use": None, "cap_total": None, "cap_unit": None},
    "scope": {"kind": "region", "store_ids": [], "prefecture_codes": ["8", "33"], "municipality": "岡山市"},
    "period": {"start": "2026-10-01", "end": "2026-10-31"},
    "entry": {"required": False, "url": None}, "conditions": None, "official_url": None,
    "evidence_quote": "最大10%戻ってくる", "confidence": "confirmed",
}


def test_normalize_fills_defaults():
    c = normalize(RAW, SRC)
    assert c["title"] == "最大10%戻ってくる"
    assert c["period"] == {"start": "2026-10-01T00:00:00+09:00", "end": "2026-10-31T23:59:59+09:00"}
    assert c["scope"]["prefecture_codes"] == ["08", "33"]
    assert c["official_url"] == SRC["url"]
    assert c["id"].startswith("cp-") and len(c["id"]) == 13


def test_secondary_is_unconfirmed():
    assert normalize(RAW, dict(SRC, kind="secondary"))["confidence"] == "unconfirmed"


def test_utc_converted_to_jst():
    raw = dict(RAW, period={"start": "2026-09-30T15:00:00Z", "end": "2026-10-31T14:59:59Z"})
    c = normalize(raw, SRC)
    assert c["period"]["start"] == "2026-10-01T00:00:00+09:00"


def test_match_same_changed_new():
    a = normalize(RAW, SRC)
    assert match_existing(a, [])[0] == "new"
    b = normalize(RAW, SRC)
    b["title"] = "表記ゆれしたタイトル"
    assert match_existing(b, [a])[0] == "same"
    c = normalize(dict(RAW, benefit=dict(RAW["benefit"], rate_max=15)), SRC)
    kind, ex = match_existing(c, [a])
    assert kind == "changed" and ex is a
    updated = apply_update(a, c)
    assert updated["id"] == a["id"] and updated["benefit"]["rate_max"] == 15


def test_different_campaigns_same_start_are_not_merged():
    # 同じ決済・同じ開始日でも、中身が違えば別キャンペーン（2026-09-26 のレビューで見つかった取り違え）
    a = normalize(dict(RAW, title="対象のお店で最大5%戻ってくる", evidence_quote="対象のお店でPayPay払いすると最大5%戻ってくる",
                       benefit=dict(RAW["benefit"], rate_max=5)), SRC)
    b = normalize(dict(RAW, title="コンビニで最大10%戻ってくる", evidence_quote="コンビニで最大10%戻ってくるキャンペーン",
                       benefit=dict(RAW["benefit"], rate_max=10)), SRC)
    assert match_existing(b, [a])[0] == "new"


def test_different_detail_pages_are_different_campaigns():
    a = normalize(dict(RAW, official_url="https://paypay.ne.jp/event/a/"), SRC)
    b = normalize(dict(RAW, official_url="https://paypay.ne.jp/event/b/"), SRC)
    assert match_existing(b, [a])[0] == "new"
