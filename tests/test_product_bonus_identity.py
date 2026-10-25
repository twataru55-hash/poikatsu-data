"""Observed Arclands pack sizes must survive sequential adoption and rechecks."""
import json
from copy import deepcopy
from pathlib import Path

from candidates import apply_update, match_existing, normalize


def products():
    raw = json.loads((Path(__file__).parent / "fixtures/arclands-products.json").read_text(encoding="utf-8-sig"))
    return [normalize(c, {"id": "rakuten-pointcard-campaign--c809378c", "kind": "official"})
            for c in raw["campaigns"]]


def test_six_real_pack_sizes_survive_sequential_ingestion():
    adopted = []
    for c in products():
        kind, existing = match_existing(c, adopted)
        if kind == "new":
            adopted.append(c)
        else:
            adopted = [apply_update(existing, c) if x is existing else x for x in adopted]
    assert len(adopted) == 6
    assert len({c["id"] for c in adopted}) == 6
    assert [c["title"] for c in adopted] == [c["title"] for c in products()]
    assert [c["benefit"]["rate_text"] for c in adopted] == [c["benefit"]["rate_text"] for c in products()]


def test_same_product_reward_changes_keep_its_id_and_neighbours():
    adopted = products()
    for original in adopted:
        assert match_existing(deepcopy(original), adopted) == ("same", original)
        changed = deepcopy(original)
        changed["benefit"]["rate_text"] = "対象品400楽天ポイント"
        kind, found = match_existing(changed, adopted)
        assert (kind, found) == ("changed", original)
        updated = apply_update(found, changed)
        assert updated["id"] == original["id"]
        assert all(c["benefit"]["rate_text"] != "対象品400楽天ポイント" for c in adopted)


def test_store100_different_products_with_identical_table_prose_do_not_merge():
    first = products()[0]
    first.update(official_url="https://store100.lawson.co.jp/point/bonus/index.html", title="温泉たまご")
    second = deepcopy(first)
    second["title"] = "とろり半熟トッピングたまご"
    assert match_existing(second, [first]) == ("new", None)
