"""依頼の公平な順番・回答されない依頼の打ち切り・ログイン画面への転送の扱い。"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import prepare  # noqa: E402
import validate  # noqa: E402
from test_manual_e2e import PREPARE, INGEST, REPO, read, run  # noqa: E402


def _entry(sid, group):
    return ({"id": sid}, "text", "h-" + sid, group)


def test_fair_order_round_robin_and_oldest_first():
    pending = [_entry(f"a--{i}", "a") for i in range(10)] + [_entry(f"b--{i}", "b") for i in range(3)] \
        + [_entry(f"c--{i}", "c") for i in range(3)]
    state = {"c--2": {"deferred_since": "2026-09-28T09:01:00+09:00"},
             "b--1": {"deferred_since": "2026-09-29T09:01:00+09:00"}}
    ids = [e[0]["id"] for e in prepare.fair_order(pending, state)]
    assert len(ids) == 16 and len(set(ids)) == 16
    # 一番古く後回しにされた c--2 が先頭、次に b の後回し分
    assert ids[:3] == ["c--2", "b--1", "a--0"]
    # 上位6件に3つの取得元が2件ずつ入る（1つの取得元が枠を独占しない）
    top = ids[:6]
    assert sorted(i.split("--")[0] for i in top) == ["a", "a", "b", "b", "c", "c"]


def _fresh_repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "work", "__pycache__", ".pytest_cache", "inbox"))
    for p in ["data/campaigns.json", "data/rejected.json"]:
        (repo / p).write_text("[]", encoding="utf-8")
    for p in ["data/holds.json", "data/snapshots.json", "data/source_status.json", "data/request_state.json"]:
        (repo / p).write_text("{}", encoding="utf-8")
    (repo / "reports" / "runs.jsonl").unlink(missing_ok=True)
    shutil.rmtree(repo / "data" / "inbox_done", ignore_errors=True)
    return repo


def test_unanswered_is_counted_and_given_up(tmp_path):
    repo = _fresh_repo(tmp_path)
    prep = json.loads(run(repo, PREPARE))
    queue = read(repo, f"inbox/{prep['run_id']}/queue.json")
    target = queue["items"][0]

    # 回答を1件も書かずに取り込む → 全件が「回答されなかった」として数えられ、報告に出る
    ing = json.loads(run(repo, INGEST))
    assert ing["ok"] and set(ing["unanswered"]) == {i["source_id"] for i in queue["items"]}
    state = read(repo, "data/request_state.json")
    assert state[target["source_id"]]["unanswered"] == 1
    assert state[target["source_id"]]["hash"] == target["hash"]
    report = next((repo / "reports").glob("run-*.md")).read_text(encoding="utf-8")
    assert "回答されなかった依頼" in report and target["source_id"] in report

    # 同じ内容のまま3回回答されなければ、ページが変わるまで依頼しない
    state[target["source_id"]]["unanswered"] = 3
    (repo / "data/request_state.json").write_text(json.dumps(state), encoding="utf-8")
    prep2 = json.loads(run(repo, PREPARE))
    q2 = read(repo, f"inbox/{prep2['run_id']}/queue.json")
    assert target["source_id"] in q2["gave_up"]
    assert target["source_id"] not in [i["source_id"] for i in q2["items"]]

    # ページの内容が変われば（hash が違えば）また依頼する
    state[target["source_id"]]["hash"] = "changed"
    (repo / "data/request_state.json").write_text(json.dumps(state), encoding="utf-8")
    shutil.rmtree(repo / "inbox" / prep2["run_id"])
    prep3 = json.loads(run(repo, PREPARE))
    q3 = read(repo, f"inbox/{prep3['run_id']}/queue.json")
    assert target["source_id"] in [i["source_id"] for i in q3["items"]]


class _Resp:
    def __init__(self, url, status=200):
        self.url, self.status_code = url, status


class _Sess:
    def __init__(self, final):
        self.final = final

    def head(self, url, **kw):
        return _Resp(self.final)

    get = head


def _checker(monkeypatch, final):
    import common
    monkeypatch.setattr(common, "http_session", lambda url, config: _Sess(final))
    return validate.http_url_checker({"validation": {"login_redirect_hosts": ["login.account.rakuten.com"]}})


def test_login_redirect_from_official_entry_is_ok(monkeypatch):
    check = _checker(monkeypatch, "https://login.account.rakuten.com/sso/authorize?client_id=x")
    assert check("https://oubo.rakuten.co.jp/apply/campaign/x", {"rakuten.co.jp"}) == (True, "")


def test_login_redirect_from_unofficial_url_is_not_ok(monkeypatch):
    check = _checker(monkeypatch, "https://login.account.rakuten.com/sso/authorize?client_id=x")
    ok, why = check("https://example.com/entry", {"rakuten.co.jp"})
    assert not ok and "公式外へ転送" in why


def test_redirect_to_other_host_still_held(monkeypatch):
    check = _checker(monkeypatch, "https://evil.example.com/login")
    ok, why = check("https://oubo.rakuten.co.jp/apply/campaign/x", {"rakuten.co.jp"})
    assert not ok and "公式外へ転送" in why


def test_expired_hold_is_closed_without_reject(tmp_path, monkeypatch):
    import copy
    import common
    import holds
    from test_unknown_fields import BASE, SOURCE

    ended = copy.deepcopy(BASE)
    ended["id"], ended["period"]["end"] = "cp-ended", "2026-09-01T23:59:59+09:00"
    active = copy.deepcopy(BASE)
    active["id"], active["period"]["end"] = "cp-active", "2099-12-31T23:59:59+09:00"
    unknown = copy.deepcopy(BASE)
    unknown["id"], unknown["period"]["end"] = "cp-unknown", None
    issues = [{"number": n, "labels": [{"name": "hold"}], "created_at": "2026-09-30T00:00:00Z",
               "body": holds.hold_body(c, ["V12"], SOURCE)} for n, c in ((1, ended), (2, active), (3, unknown))]

    class FakeGitHub:
        def __init__(self):
            self.closed = []
        def ensure_labels(self): pass
        def list_issues(self, label): return issues
        def close(self, number, body=None): self.closed.append(number)
        def add_labels(self, *a): pass

    gh = FakeGitHub()
    monkeypatch.setenv("POIKATSU_NOW", "2026-10-01T09:00:00+09:00")
    monkeypatch.setattr(holds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(holds, "GitHub", lambda: gh)
    monkeypatch.setattr(holds, "load_config", lambda: {})
    monkeypatch.setattr(common, "load_master", lambda: {"brands": [], "stores": []})
    monkeypatch.setattr(common, "load_sources", lambda: [])
    adopted = copy.deepcopy(BASE)
    adopted["id"], adopted["period"]["end"] = "cp-adopted", "2099-12-31T23:59:59+09:00"
    issues.append({"number": 4, "labels": [{"name": "hold"}], "created_at": "2026-09-30T00:00:00Z",
                   "body": holds.hold_body(adopted, ["V12"], SOURCE)})
    common.save_json(tmp_path / "campaigns.json", [adopted])
    common.save_json(tmp_path / "holds.json", {c["id"]: {"issue": n} for n, c in
                                               ((1, ended), (2, active), (3, unknown), (4, adopted))})
    result = holds.process()
    assert result["expired"] == 1 and result["resolved"] == 1 and sorted(gh.closed) == [1, 4]
    assert set(common.load_json(tmp_path / "holds.json")) == {"cp-active", "cp-unknown"}
    assert common.load_json(tmp_path / "rejected.json") == []
    assert [c["id"] for c in common.load_json(tmp_path / "campaigns.json")] == ["cp-adopted"]
