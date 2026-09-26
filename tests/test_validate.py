"""機械チェック V01〜V13 の合格例・不合格例。"""
import copy

import pytest

from validate import Context, validate_candidate

CONFIG = {
    "validation": {"evidence_window": 600, "check_urls": True, "max_days_ahead": 400},
    "hold_rules": {"rate_max_at_or_above": 20, "cap_total_at_or_above": 10000},
    "banned_words": ["絶対", "必ず", "最強", "誰でも", "今すぐ", "損する"],
}
BRANDS = {"paypay": {"id": "paypay", "domains": ["paypay.ne.jp"]}}
STORES = {"store-a": {"id": "store-a", "domains": ["store-a.example.jp"]}}
SOURCE = {"id": "paypay-cp", "kind": "official", "domains": ["paypay.ne.jp"], "url": "https://paypay.ne.jp/event/"}
PAGE = "キャンペーン一覧\n対象店舗でPayPay払いすると最大10%戻ってくる\n期間：2026年10月1日～2026年10月31日\n付与上限：1回300ポイント"

BASE = {
    "id": "cp-0123456789",
    "type": "reward",
    "brand_ids": ["paypay"],
    "title": "対象店舗で最大10%戻ってくる",
    "benefit": {"rate_max": 10, "rate_text": "最大10%還元", "cap_per_use": 300, "cap_total": None, "cap_unit": "pt"},
    "scope": {"kind": "stores", "store_ids": ["store-a"], "prefecture_codes": [], "municipality": None},
    "period": {"start": "2026-10-01T00:00:00+09:00", "end": "2026-10-31T23:59:59+09:00"},
    "entry": {"required": False, "url": None},
    "conditions": "1回300ポイントまで",
    "official_url": "https://paypay.ne.jp/event/sample/",
    "evidence_quote": "対象店舗でPayPay払いすると最大10%戻ってくる",
    "confidence": "confirmed",
    "source_id": "paypay-cp",
    "first_seen": "2026-10-01",
    "last_verified": "2026-10-01",
    "status_override": None,
}


def ok_checker(url, allowed):
    return True, ""


def ctx(**kw):
    base = dict(brands=BRANDS, stores=STORES, source=SOURCE, page_text=PAGE, config=CONFIG, url_checker=ok_checker)
    base.update(kw)
    return Context(**base)


def with_(**changes):
    c = copy.deepcopy(BASE)
    for path, value in changes.items():
        cur = c
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = value
    return c


def codes(reasons):
    return {r[:3] for r in reasons}


def test_accept_base():
    assert validate_candidate(copy.deepcopy(BASE), ctx()) == ("accept", [])


@pytest.mark.parametrize("bad", [with_(id="bad-id"), with_(type="unknown"), with_(title="あ" * 41)])
def test_v01_discard(bad):
    decision, reasons = validate_candidate(bad, ctx())
    assert decision == "discard" and codes(reasons) == {"V01"}


def test_v02_stores_empty():
    assert "V02" in codes(validate_candidate(with_(scope__store_ids=[]), ctx())[1])


def test_v02_entry_without_url():
    assert "V02" in codes(validate_candidate(with_(entry__required=True), ctx())[1])


def test_v03_unknown_brand():
    assert "V03" in codes(validate_candidate(with_(brand_ids=["nobrand"]), ctx())[1])


def test_v04_foreign_domain():
    assert "V04" in codes(validate_candidate(with_(official_url="https://matome.example.com/a"), ctx())[1])


def test_v05_url_not_open():
    d, r = validate_candidate(copy.deepcopy(BASE), ctx(url_checker=lambda u, a: (False, "HTTP 404")))
    assert d == "hold" and "V05" in codes(r)


def test_v06_quote_not_in_page():
    assert "V06" in codes(validate_candidate(with_(evidence_quote="ページにない文章"), ctx())[1])


def test_v06_quote_matches_after_normalization():
    # 全角・空白の違いは同一とみなす
    c = with_(evidence_quote="対象店舗で PayPay払いすると最大１０％戻ってくる")
    assert validate_candidate(c, ctx())[0] == "accept"


def test_v07_rate_not_near():
    assert "V07" in codes(validate_candidate(with_(benefit__rate_max=7), ctx())[1])


def test_v07_date_not_near():
    page = "対象店舗でPayPay払いすると最大10%戻ってくる"
    assert "V07" in codes(validate_candidate(copy.deepcopy(BASE), ctx(page_text=page))[1])


def test_v08_start_after_end():
    c = with_(period__start="2026-11-01T00:00:00+09:00")
    assert "V08" in codes(validate_candidate(c, ctx())[1])


def test_v08_already_ended():
    c = with_(period__start="2026-09-01T00:00:00+09:00", period__end="2026-09-10T23:59:59+09:00")
    assert "V08" in codes(validate_candidate(c, ctx(page_text=None))[1])


def test_v09_out_of_range():
    assert "V09" in codes(validate_candidate(with_(benefit__rate_max=150), ctx(page_text=None))[1])


def test_v10_big_rate():
    assert "V10" in codes(validate_candidate(with_(benefit__rate_max=25), ctx(page_text=None))[1])


def test_v10_big_cap():
    assert "V10" in codes(validate_candidate(with_(benefit__cap_total=20000), ctx())[1])


def test_v11_change_is_held():
    assert "V11" in codes(validate_candidate(with_(type="change"), ctx())[1])


def test_v12_unconfirmed():
    assert "V12" in codes(validate_candidate(with_(confidence="unconfirmed"), ctx())[1])


def test_v12_secondary_source():
    src = dict(SOURCE, kind="secondary")
    assert "V12" in codes(validate_candidate(copy.deepcopy(BASE), ctx(source=src))[1])


def test_v13_banned_word():
    assert "V13" in codes(validate_candidate(with_(title="絶対お得な最大10%"), ctx())[1])


def test_v13_first_person():
    assert "V13" in codes(validate_candidate(with_(conditions="私は毎回使っている"), ctx())[1])


def test_approve_path_skips_page_checks():
    only = {"V01", "V02", "V03", "V04", "V05", "V08", "V09"}
    c = with_(evidence_quote="ページにない文章", type="change")
    assert validate_candidate(c, ctx(page_text=None, only=only)) == ("accept", [])
