"""停止の検知（Issue の重複防止と自動クローズ）と、成功「日数」の集計。"""
import json
from datetime import datetime, timedelta, timezone

import audit
import common
import watchdog

JST = timezone(timedelta(hours=9))


class FakeGH:
    def __init__(self):
        self.issues = {}  # number -> {"title", "state"}
        self.n = 0

    def ensure_labels(self):
        pass

    def create_issue(self, title, body, labels):
        self.n += 1
        self.issues[self.n] = {"number": self.n, "title": title, "state": "open", "labels": labels}
        return self.n

    def list_issues(self, label, state="open"):
        return [i for i in self.issues.values() if i["state"] == state and label in i["labels"]]

    def close(self, number, body=None):
        self.issues[number]["state"] = "closed"


def _setup(tmp_path, monkeypatch, runs):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(watchdog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(watchdog, "read_run_log", lambda: runs)


def _run(iso, ok=True, mode="ingest"):
    return {"date": iso, "ok": ok, "mode": mode}


def test_watchdog_open_once_and_close(tmp_path, monkeypatch):
    gh = FakeGH()
    runs = [_run("2026-10-01T06:40:00+09:00")]
    _setup(tmp_path, monkeypatch, runs)
    monkeypatch.setenv("POIKATSU_NOW", "2026-10-03T12:00:00+09:00")  # 53時間停止
    assert watchdog.main(gh)["action"] == "opened"
    assert watchdog.main(gh)["action"] == "none"       # 2回目は作らない
    (tmp_path / "watchdog.json").unlink()              # 状態ファイルが消えても
    assert watchdog.main(gh)["action"] == "none"       # 開いている Issue を見つけて重複させない
    assert len(gh.list_issues("anomaly")) == 1
    runs.append(_run("2026-10-04T06:40:00+09:00"))     # 再開
    monkeypatch.setenv("POIKATSU_NOW", "2026-10-04T12:00:00+09:00")
    assert watchdog.main(gh)["action"] == "closed"
    assert gh.list_issues("anomaly") == []
    runs.append(_run("2026-10-04T06:40:00+09:00"))
    monkeypatch.setenv("POIKATSU_NOW", "2026-10-07T12:00:00+09:00")  # 再停止 → 新しく1件
    assert watchdog.main(gh)["action"] == "opened"
    assert len(gh.list_issues("anomaly")) == 1


def test_watchdog_silent_before_first_run(tmp_path, monkeypatch):
    gh = FakeGH()
    _setup(tmp_path, monkeypatch, [])
    assert watchdog.main(gh)["action"] == "none" and gh.issues == {}


def test_success_days_counts_days_not_runs():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=JST)
    log = [_run("2026-10-14T06:40:00+09:00") for _ in range(13)]  # 同じ日に13回
    r = audit.success_days(log, now, 14)
    assert r["counted"] == 1 and r["ok"] == 1


def test_success_days_jst_boundary_and_missing():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=JST)
    log = [_run(f"2026-10-{d:02d}T06:40:00+09:00") for d in range(1, 15) if d != 5]
    log.append(_run("2026-10-05T23:30:00+00:00"))  # UTC 10/5 23:30 = JST 10/6 → 10/5 の成功にはならない
    log.append(_run("2026-10-07T06:40:00+09:00", ok=False))
    r = audit.success_days(log, now, 14)
    assert r["counted"] == 14 and r["ok"] == 13 and r["missing"] == ["2026-10-05"]
