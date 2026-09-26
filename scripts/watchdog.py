"""【GitHub側】更新が止まっていないかを毎日確かめる。

- 最後に成功した取り込み（または API 方式の日次実行）から36時間以上たっていたら、Issue「[停止] …」を作る
  すでに開いている「[停止]」Issue があれば新しく作らない（重複防止）
- 再開したら、その Issue にコメントして自動で閉じる
- まだ一度も動いていない（導入直後）ときは何もしない
"""
from __future__ import annotations

import json
from datetime import timedelta

from common import DATA_DIR, load_json, now_jst, parse_dt, read_run_log, save_json
from holds import GitHub

LIMIT_HOURS = 36
TITLE = "[停止] データの更新が止まっています"


def _open_stop_issues(gh: GitHub) -> list[int]:
    return [i["number"] for i in gh.list_issues("anomaly") if str(i.get("title", "")).startswith("[停止]")]


def main(gh: GitHub | None = None) -> dict:
    gh = gh or GitHub()
    runs = [r for r in read_run_log() if r.get("ok") and r.get("mode") in ("ingest", "api")]
    last = parse_dt(runs[-1]["date"]) if runs else None
    state = load_json(DATA_DIR / "watchdog.json", {})
    stopped = last is not None and now_jst() - last > timedelta(hours=LIMIT_HOURS)
    action = "none"

    if stopped:
        existing = _open_stop_issues(gh)
        if existing:
            state["issue_number"] = existing[0]
        elif not state.get("issue_number"):
            gh.ensure_labels()
            state["issue_number"] = gh.create_issue(
                TITLE,
                f"最後に更新できたのは {last.isoformat()} です。\n\n"
                "- PC が起動していて、Codex（ChatGPT）デスクトップアプリが開いているか\n"
                "- スケジュール済みタスクが一時停止になっていないか\n"
                "- PC 側の作業でエラーが出ていないか（タスクの結果）\n\nを確認してください。"
                "再開すると、この Issue は自動で閉じます。",
                ["anomaly"],
            )
            action = "opened"
    else:
        numbers = set(_open_stop_issues(gh))
        if state.get("issue_number"):
            numbers.add(state["issue_number"])
        for n in sorted(x for x in numbers if x):
            gh.close(n, f"更新の再開を確認しました（最終更新 {last.isoformat() if last else '不明'}）。自動で閉じます。")
            action = "closed"
        state.pop("issue_number", None)

    state["stopped"] = stopped
    state["last_ok"] = last.isoformat() if last else None
    state.pop("issue_open", None)  # 旧形式のキー
    save_json(DATA_DIR / "watchdog.json", state)
    return {"action": action, **state}


if __name__ == "__main__":
    from common import setup_utf8_stdout

    setup_utf8_stdout()
    print(json.dumps(main(), ensure_ascii=False))
