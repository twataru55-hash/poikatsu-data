"""【GitHub側】更新が止まっていないかを毎日確かめる。

最後に成功した取り込み（または API 方式の日次実行）から36時間以上たっていたら、
Issue「[停止] …」を1回だけ作る。再開したら自動で解除する。
"""
from __future__ import annotations

import json
from datetime import timedelta

from common import DATA_DIR, load_json, now_jst, parse_dt, read_run_log, save_json
from holds import GitHub

LIMIT_HOURS = 36


def main() -> dict:
    runs = [r for r in read_run_log() if r.get("ok") and r.get("mode") in ("ingest", "api")]
    last = parse_dt(runs[-1]["date"]) if runs else None
    state = load_json(DATA_DIR / "watchdog.json", {"issue_open": False})
    # まだ一度も動いていない（導入直後）ときは知らせない
    stopped = last is not None and now_jst() - last > timedelta(hours=LIMIT_HOURS)
    if stopped and not state.get("issue_open"):
        gh = GitHub()
        gh.ensure_labels()
        gh.create_issue(
            "[停止] データの更新が止まっています",
            f"最後に更新できたのは {last.isoformat() if last else '（記録なし）'} です。\n\n"
            "- PC が起動していて、ChatGPT デスクトップアプリが開いているか\n"
            "- ChatGPT のスケジュール済みタスクが一時停止になっていないか\n"
            "- PC 側の作業でエラーが出ていないか（ChatGPT のタスク結果）\n\nを確認してください。",
            ["anomaly"],
        )
        state["issue_open"] = True
    elif not stopped:
        state["issue_open"] = False
    state["last_ok"] = last.isoformat() if last else None
    save_json(DATA_DIR / "watchdog.json", state)
    return {"stopped": stopped, **state}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
