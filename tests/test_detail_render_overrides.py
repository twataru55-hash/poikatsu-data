"""Exact render overrides must reach discovery, prompts and ingestion rechecks."""
import json

import pytest

import ingest
from common import load_sources
from discovery import source_for_detail
from prepare import detail_source
from jsonschema import Draft202012Validator
from common import ROOT, load_json


CASES = [
    ("welcia-offers", "https://m.e-welcia.com/drug/202610_autumn_fair", "js"),
    ("welcia-offers", "https://m.e-welcia.com/drug/202610_autumn_fair?utm_source=test", "js"),
    ("welcia-offers", "https://m.e-welcia.com/drug/another", "static"),
    ("vpoint-campaign", "https://lot.tsite.jp/", "js"),
    ("vpoint-campaign", "https://web.tsite.jp/cpn/another/", "static"),
    ("welcia-offers", "https://m.e-welcia.com/drug/202610_autumn_fair?campaign=other", "static"),
]


@pytest.mark.parametrize("key,value", [("http://m.e-welcia.com/", "js"),
                                       ("https://m.e-welcia.com/", "unknown"),
                                       ("https://m.e-welcia.com/", None)])
def test_schema_rejects_unsafe_or_unknown_override_settings(key, value):
    sources = load_sources()
    sources[0]["follow"]["render_overrides"] = {key:value}
    validator = Draft202012Validator(load_json(ROOT / "schema/sources.schema.json"))
    assert not validator.is_valid(sources)


@pytest.mark.parametrize("parent_id,url,expected", CASES)
def test_discovery_and_prompt_preserve_exact_url_override(parent_id, url, expected):
    parent = next(s for s in load_sources() if s["id"] == parent_id)
    assert source_for_detail(parent, url)["render"] == expected
    assert detail_source(parent, url)["render"] == expected
    assert source_for_detail(parent, url)["domains"] == parent["domains"]


@pytest.mark.parametrize("parent_id,url,expected", CASES[:1] + CASES[2:5])
def test_ingest_recheck_uses_same_render_override(monkeypatch, tmp_path, parent_id, url, expected):
    parent = next(s for s in load_sources() if s["id"] == parent_id)
    run_dir = tmp_path / "inbox" / "20261025-1100"
    run_dir.mkdir(parents=True)
    queue = {"run_id":run_dir.name, "created_at":"2026-10-25T11:00:00+09:00",
             "items":[{"source_id":"test-detail", "parent":parent_id, "url":url,
                       "hash":"test-hash", "answer_file":"answer.json"}]}
    (run_dir / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
    (tmp_path / "answer.json").write_text('{"campaigns":[]}', encoding="utf-8")
    original_load = ingest.load_json
    monkeypatch.setattr(ingest, "ROOT", tmp_path)
    monkeypatch.setattr(ingest, "DONE", tmp_path / "done")
    monkeypatch.setattr(ingest, "pending_runs", lambda:[run_dir])
    monkeypatch.setattr(ingest, "load_config", lambda:{"enabled":True})
    monkeypatch.setattr(ingest, "load_sources", lambda:[parent])
    monkeypatch.setattr(ingest, "prepare_master", lambda *_:{})
    monkeypatch.setattr(ingest, "GitHub", lambda:object())
    monkeypatch.setattr(ingest, "load_json", lambda path, default=None: original_load(path, default) if path.name == "queue.json" else ({} if path.name != "campaigns.json" else []))
    monkeypatch.setattr(ingest, "save_json", lambda *_:None)
    monkeypatch.setattr(ingest, "http_url_checker", lambda *_:None)
    monkeypatch.setattr(ingest, "process_items", lambda items,src,text,campaigns,*_:campaigns)
    monkeypatch.setattr(ingest, "finalize", lambda gh,config,campaigns,logs,stats:stats)
    seen = []
    def fetch(src, config, fetcher=None):
        seen.append(src["render"])
        return "公式本文"
    monkeypatch.setattr(ingest, "fetch_text", fetch)
    ingest.run()
    assert seen == [expected]
