"""配信ファイルの中身と異常検知。"""
from build import detect_anomaly, make_bundle

CONFIG = {"bundle": {"upcoming_within_days": 30}, "anomaly": {"max_change_ratio": 0.3, "min_items_for_ratio": 10, "max_removed_per_run": 10}}


def camp(i, start="2026-10-01T00:00:00+09:00", end="2026-10-31T23:59:59+09:00"):
    return {"id": f"cp-{i:010x}", "period": {"start": start, "end": end}, "status_override": None,
            "evidence_quote": "x", "source_id": "s", "first_seen": "2026-10-01"}


MASTER = {
    "brands": [], "pr_links": [{"brand_id": "a", "active": False}], "phrases": {},
    "stores": [{"id": "s1", "domains": ["x"], "accepted_source": {}, "base_rewards": [
        {"brand_id": "a", "verified": True, "evidence_quote": "q"}, {"brand_id": "b", "evidence_quote": "q"}]}],
    "recurring": [{"id": "rc-a", "verified": True, "evidence_quote": "q"}, {"id": "rc-b", "verified": False, "evidence_quote": "q"}],
}


def test_bundle_filters():
    cs = [camp(1), camp(2, end="2026-10-01T23:59:59+09:00"), camp(3, start="2026-12-20T00:00:00+09:00", end="2026-12-31T23:59:59+09:00")]
    b = make_bundle(cs, MASTER, CONFIG, "staging")
    assert [c["id"] for c in b["campaigns"]] == [camp(1)["id"]]
    assert "evidence_quote" not in b["campaigns"][0]
    assert [r["brand_id"] for r in b["stores"][0]["base_rewards"]] == ["a"]
    assert "domains" not in b["stores"][0] and "accepted_source" not in b["stores"][0]
    assert [r["id"] for r in b["recurring"]] == ["rc-a"]
    assert b["pr_links"] == []


def test_anomaly_ratio():
    old = {"campaigns": [camp(i) for i in range(20)]}
    new = {"campaigns": [camp(i) for i in range(10)]}
    problems = detect_anomaly(new, old, CONFIG)
    assert any("急変" in p for p in problems)


def test_no_anomaly_small_change():
    old = {"campaigns": [camp(i) for i in range(20)]}
    new = {"campaigns": [camp(i) for i in range(18)]}
    assert detect_anomaly(new, old, CONFIG) == []


def test_no_anomaly_first_time():
    assert detect_anomaly({"campaigns": [camp(1)]}, None, CONFIG) == []
