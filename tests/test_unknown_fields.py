"""不明値の保持・保留・承認拒否・配信拒否を実際の処理経路で検査する。"""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

import build
import common
import holds
import pipeline
from campaign_shape import publication_errors
from candidates import normalize
from test_build import CONFIG as BUILD_CONFIG, MASTER
from test_validate import BASE, SOURCE, PAGE, ctx
from validate import validate_candidate

FIELDS = [("entry", "required"), ("period", "start"), ("period", "end")]


def raw_candidate(c):
    schema = common.load_json(common.SCHEMA_DIR / "llm_output.schema.json")
    keys = schema["properties"]["campaigns"]["items"]["properties"]
    return {k: copy.deepcopy(c[k]) for k in keys if k in c}


@pytest.mark.parametrize("parent,key", FIELDS)
def test_unknown_survives_normalization_and_is_held(parent, key):
    c = copy.deepcopy(BASE)
    c[parent][key] = None
    raw = raw_candidate(c)
    validator = Draft202012Validator(common.load_json(common.SCHEMA_DIR / "llm_output.schema.json"))
    assert validator.is_valid({"campaigns": [raw]})
    normalized = normalize(raw, SOURCE)
    assert normalized[parent][key] is None
    decision, reasons = validate_candidate(normalized, ctx())
    assert decision == "hold" and any(f"{parent}.{key}" in r for r in reasons)
    # 公開用スキーマは緩めない。
    assert publication_errors(normalized)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, [], {}])
def test_bad_entry_type_is_not_coerced_or_held(value):
    raw = raw_candidate(BASE)
    raw["entry"]["required"] = value
    c = normalize(raw, SOURCE)
    assert type(c["entry"]["required"]) is type(value)
    assert validate_candidate(c, ctx())[0] == "discard"


@pytest.mark.parametrize("value", [False, 0, [], {}, "未定", ""])
def test_bad_period_type_remains_discard(value):
    raw = raw_candidate(BASE)
    raw["period"]["end"] = value
    assert validate_candidate(normalize(raw, SOURCE), ctx())[0] == "discard"


def test_unknown_does_not_hide_malformed_data():
    c = copy.deepcopy(BASE)
    c["entry"]["required"] = None
    del c["period"]["start"]
    assert validate_candidate(c, ctx())[0] == "discard"


def test_unknown_issue_does_not_say_entry_unnecessary():
    c = copy.deepcopy(BASE)
    c["entry"]["required"] = c["period"]["end"] = None
    body = holds.hold_body(c, ["V02 不明"], SOURCE)
    assert "| エントリー | 不明" in body and "| エントリー | 不要" not in body
    assert "〜 不明" in body
    stored = json.loads(holds.JSON_BLOCK.search(body).group(1))
    assert stored["entry"]["required"] is None and stored["period"]["end"] is None


@pytest.mark.parametrize("parent,key", FIELDS)
def test_approve_process_cannot_publish_unknown(tmp_path, monkeypatch, parent, key):
    c = copy.deepcopy(BASE)
    c[parent][key] = None
    class FakeGitHub:
        def __init__(self):
            self.comments, self.removed = [], []
        def ensure_labels(self): pass
        def list_issues(self, label):
            return [{"number": 1, "labels": [{"name": "approve"}, {"name": "hold"}],
                     "body": holds.hold_body(c, ["V02 不明"], SOURCE)}]
        def comment(self, number, body): self.comments.append(body)
        def remove_label(self, number, label): self.removed.append(label)
        def close(self, *args): pytest.fail("不完全な候補を承認してはいけない")
    gh = FakeGitHub()
    monkeypatch.setattr(holds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(holds, "GitHub", lambda: gh)
    monkeypatch.setattr(holds, "load_config", lambda: {})
    monkeypatch.setattr(common, "load_master", lambda: {"brands": [], "stores": []})
    monkeypatch.setattr(common, "load_sources", lambda: [])
    common.save_json(tmp_path / "holds.json", {c["id"]: {"issue": 1}})
    result = holds.process()
    assert result["approved"] == 0 and result["failed"] == 1
    assert common.load_json(tmp_path / "campaigns.json") == []
    assert c["id"] in common.load_json(tmp_path / "holds.json")
    assert gh.removed == ["approve"] and any("V02" in x for x in gh.comments)


@pytest.mark.parametrize("parent,key", FIELDS)
def test_build_preserves_all_files_on_unknown(tmp_path, monkeypatch, parent, key):
    c = copy.deepcopy(BASE)
    c[parent][key] = None
    data, dist = tmp_path / "data", tmp_path / "dist"
    common.save_json(data / "campaigns.json", [c])
    for channel in ("staging", "live"):
        common.save_json(dist / channel / "poikatsu.json", {"previous": channel})
        with pytest.raises(ValueError, match="必須値"):
            build.make_bundle([c], MASTER, BUILD_CONFIG, channel)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(build, "DATA_DIR", data)
    monkeypatch.setattr(build, "DIST_DIR", dist)
    monkeypatch.setattr(build, "load_config", lambda: {"publish_to_live": True})
    monkeypatch.setattr(build, "load_master", lambda: MASTER)
    result = build.build()
    assert result["error"] and not result["staging"] and not result["live"]
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_same_candidate_cannot_bypass_unknown_gate(monkeypatch):
    raw = raw_candidate(BASE)
    raw["entry"]["required"] = None
    existing = normalize(raw, SOURCE)
    existing["last_verified"] = "2026-09-01"
    seen = []
    def capture_hold(gh, candidate, reasons, source):
        seen.append((candidate, reasons))
        return True
    monkeypatch.setattr(pipeline, "file_hold", capture_hold)
    stats = pipeline.new_stats("test", {})
    pipeline.process_items([raw], SOURCE, PAGE, [existing], {"brands": [], "stores": []},
                           {}, None, stats, [], None)
    assert stats["same"] == 0 and stats["held"] == 1
    assert existing["last_verified"] == "2026-09-01"
    assert seen[0][0]["entry"]["required"] is None
