"""採用済みと同じIDでも、内容が異なる保留を自動で閉じない。"""
import copy

import pytest

import common
import holds
from test_validate import BASE, SOURCE


def _process(tmp_path, monkeypatch, candidate, adopted):
    issue = {"number": 1, "labels": [{"name": "hold"}],
             "created_at": "2026-09-30T00:00:00Z",
             "body": holds.hold_body(candidate, ["V12"], SOURCE)}

    class FakeGitHub:
        def __init__(self):
            self.closed = []

        def ensure_labels(self): pass
        def list_issues(self, label): return [issue]
        def close(self, number, body=None): self.closed.append(number)
        def add_labels(self, *args): pass

    gh = FakeGitHub()
    monkeypatch.setenv("POIKATSU_NOW", "2026-10-01T09:00:00+09:00")
    monkeypatch.setattr(holds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(holds, "GitHub", lambda: gh)
    monkeypatch.setattr(holds, "load_config", lambda: {})
    monkeypatch.setattr(common, "load_master", lambda: {"brands": [], "stores": []})
    monkeypatch.setattr(common, "load_sources", lambda: [])
    common.save_json(tmp_path / "campaigns.json", [adopted])
    common.save_json(tmp_path / "holds.json", {candidate["id"]: {"issue": 1}})
    return holds.process(), gh


def _active():
    candidate = copy.deepcopy(BASE)
    candidate["period"]["end"] = "2099-12-31T23:59:59+09:00"
    return candidate


@pytest.mark.parametrize("field,key,value", [
    ("benefit", "rate_max", 25),
    ("benefit", "cap_total", 9999),
    ("period", "end", "2099-12-30T23:59:59+09:00"),
    ("entry", "required", True),
    ("entry", "url", "https://paypay.ne.jp/entry/other"),
    ("scope", "store_ids", ["seven-eleven"]),
    ("conditions", None, "対象店は西友・リヴィンのみ"),
    ("official_url", None, "https://paypay.ne.jp/event/other/"),
    ("entry", "required", None),
    ("period", "end", None),
])
def test_different_content_or_unknown_is_not_resolved(tmp_path, monkeypatch, field, key, value):
    adopted = _active()
    candidate = copy.deepcopy(adopted)
    if key is None:
        candidate[field] = value
    else:
        candidate[field][key] = value
    assert candidate != adopted
    result, gh = _process(tmp_path, monkeypatch, candidate, adopted)
    assert result["resolved"] == 0 and not gh.closed
    assert candidate["id"] in common.load_json(tmp_path / "holds.json")
    assert common.load_json(tmp_path / "campaigns.json") == [adopted]
    assert common.load_json(tmp_path / "rejected.json") == []


def test_same_content_with_updated_verification_is_resolved(tmp_path, monkeypatch):
    adopted = _active()
    candidate = copy.deepcopy(adopted)
    candidate["confidence"] = "unconfirmed"
    candidate["last_verified"] = "2026-09-29"
    candidate["evidence_quote"] = "前回取得した根拠文"
    result, gh = _process(tmp_path, monkeypatch, candidate, adopted)
    assert result["resolved"] == 1 and gh.closed == [1]
    assert common.load_json(tmp_path / "holds.json") == {}
    assert common.load_json(tmp_path / "campaigns.json") == [adopted]
    assert common.load_json(tmp_path / "rejected.json") == []
