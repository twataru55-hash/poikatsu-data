"""Product bonuses must survive sequential ingestion as distinct products."""
from copy import deepcopy

import pytest

from candidates import apply_update, match_existing, normalize


URLS = [
    "https://www.family.co.jp/campaign/otoku.html",
    "https://www.family.co.jp/campaign/pointplus.html",
]


def product(url, title, points=10):
    return normalize({
        "type": "bonus", "brand_ids": ["famima-point"], "title": title,
        "benefit": {"rate_max": None, "rate_text": f"対象品{points}円相当", "cap_per_use": None,
                    "cap_total": None, "cap_unit": None},
        "scope": {"kind": "stores", "store_ids": ["familymart"], "prefecture_codes": [], "municipality": None},
        "period": {"start": "2026-10-13", "end": "2026-11-16"},
        "entry": {"required": False, "url": None}, "conditions": "アプリを提示",
        "official_url": url,
        "evidence_quote": "共通のキャンペーン案内。" * 10 + title,
        "confidence": "confirmed",
    }, {"id": "familymart-test", "url": url, "kind": "official"})


@pytest.mark.parametrize("url", URLS)
def test_different_flavours_pack_sizes_and_rewards_survive(url):
    originals = [product(url, "飲料いちご味 100ml"), product(url, "飲料もも味 100ml"),
                 product(url, "飲料いちご味 200ml", 20)]
    adopted = []
    for c in originals:
        assert match_existing(c, adopted) == ("new", None)
        adopted.append(c)
    assert [c["title"] for c in adopted] == [c["title"] for c in originals]
    assert len({c["id"] for c in adopted}) == 3


@pytest.mark.parametrize("url", URLS)
def test_same_product_recheck_is_same_and_changed_reward_keeps_id(url):
    first = product(url, "飲料いちご味 100ml")
    assert match_existing(deepcopy(first), [first]) == ("same", first)
    changed = product(url, "飲料いちご味 100ml", 30)
    assert match_existing(changed, [first]) == ("changed", first)
    updated = apply_update(first, changed)
    assert updated["id"] == first["id"]
    assert updated["benefit"]["rate_text"] == "対象品30円相当"


def test_other_bonus_campaigns_keep_existing_title_variation_matching():
    first = product("https://www.family.co.jp/campaign/another.html", "元の企画名")
    second = deepcopy(first)
    second["title"] = "変更した企画名"
    assert match_existing(second, [first]) == ("same", first)
