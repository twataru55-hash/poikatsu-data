"""【GitHub側】PCから届いた回答（inbox/）を取り込み、機械チェック → 採用／保留 → 配信ファイル作成。

inbox/<run_id>/queue.json が push されると .github/workflows/ingest.yml から呼ばれる。
- 根拠文の確認（V06・V07）のため、公式ページはこちらでも取得し直す
- 回答が無い・形が壊れているページは、スナップショットを更新しない（次回PCで再依頼される）
- 処理が済んだ run は data/inbox_done/<run_id>/ に移す
"""
from __future__ import annotations

import json
import shutil
import sys

from jsonschema import Draft202012Validator

from collect import fetch_text
from common import DATA_DIR, ROOT, SCHEMA_DIR, load_config, load_json, load_sources, save_json, setup_utf8_stdout
from holds import GitHub
from pipeline import finalize, new_stats, prepare_master, process_items
from validate import http_url_checker

INBOX = ROOT / "inbox"
DONE = DATA_DIR / "inbox_done"


def pending_runs() -> list:
    if not INBOX.exists():
        return []
    return sorted(p for p in INBOX.iterdir() if p.is_dir() and (p / "queue.json").exists())


def run() -> dict:
    config = load_config()
    stats = new_stats("ingest", config)
    runs = pending_runs()
    if not runs:
        stats["ok"] = True
        stats["errors"].append("取り込む回答がない")
        return stats
    if not stats["enabled"]:
        stats["ok"] = True
        stats["errors"].append("停止スイッチ（enabled: false）のため何もしていない")
        return stats

    gh = GitHub()
    master = prepare_master(gh, stats)
    if master is None:
        return stats

    sources = {s["id"]: s for s in load_sources()}
    validator = Draft202012Validator(load_json(SCHEMA_DIR / "llm_output.schema.json"))
    campaigns = load_json(DATA_DIR / "campaigns.json", [])
    snapshots = load_json(DATA_DIR / "snapshots.json", {})
    status = load_json(DATA_DIR / "source_status.json", {})
    requests = load_json(DATA_DIR / "request_state.json", {})
    checker = http_url_checker(config)
    cand_log: list[dict] = []

    for run_dir in runs:
        queue = load_json(run_dir / "queue.json", {})
        at = queue.get("created_at")

        # 取得できた・できなかった の記録（PC側の結果）
        for f in queue.get("failed", []):
            st = status.setdefault(f["source_id"], {"fail_count": 0, "last_ok": None, "last_error": None, "issue_open": False})
            st["fail_count"] = int(st.get("fail_count", 0)) + 1
            st["last_error"] = str(f.get("error", ""))[:200]
            stats["sources_failed"] += 1
        ok_ids = list(queue.get("unchanged", [])) + [i["source_id"] for i in queue.get("items", [])] + list(queue.get("deferred", []))
        for sid in ok_ids:
            st = status.setdefault(sid, {"fail_count": 0, "last_ok": None, "last_error": None, "issue_open": False})
            st.update(fail_count=0, last_ok=at, last_error=None)
            stats["sources_ok"] += 1
        stats["llm_skipped"] += len(queue.get("deferred", []))
        for sid in queue.get("deferred", []):
            requests.setdefault(sid, {}).setdefault("deferred_since", at)
        stats["gave_up"] = sorted(set(stats.get("gave_up", [])) | set(queue.get("gave_up", [])))

        for item in queue.get("items", []):
            sid = item["source_id"]
            if item.get("parent"):
                # 一覧ページからたどった詳細ページ：親の設定を使い、URL だけ差し替える
                parent = sources.get(item["parent"])
                src = {**parent, "url": item["url"], "render": (parent.get("follow") or {}).get("render", parent.get("render"))} if parent else None
            else:
                src = sources.get(sid)
            stats["pages_changed"] += 1
            path = ROOT / item["answer_file"]
            if not src:
                stats["errors"].append(f"{sid}: sources.yaml にない取得元")
                continue
            if not path.exists():
                stats["llm_skipped"] += 1
                rq = requests.setdefault(sid, {})
                rq["unanswered"] = int(rq.get("unanswered", 0)) + 1 if rq.get("hash") == item["hash"] else 1
                rq["hash"] = item["hash"]
                rq.setdefault("deferred_since", at)  # 回答されなかったものも次回は先に回す
                stats.setdefault("unanswered", []).append(sid)
                continue
            try:
                answer = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                stats["errors"].append(f"{sid}: 回答がJSONとして読めない {e}")
                continue
            errs = list(validator.iter_errors(answer))
            if errs:
                stats["errors"].append(f"{sid}: 回答の形が違う {errs[0].message[:120]}")
                continue
            try:
                text = fetch_text(src, config)
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"{sid}: 確認用の再取得に失敗 {str(e)[:120]}（次回に再依頼）")
                continue
            stats["llm_calls"] += 1
            campaigns = process_items(answer.get("campaigns", []), src, text, campaigns, master, config,
                                      gh, stats, cand_log, checker)
            snapshots[sid] = {"hash": item["hash"], "fetched_at": at, "chars": len(text),
                              "extraction_revision": queue.get("extraction_revision", 0)}
            requests.pop(sid, None)

        DONE.mkdir(parents=True, exist_ok=True)
        target = DONE / run_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(run_dir), str(target))

    save_json(DATA_DIR / "snapshots.json", snapshots)
    save_json(DATA_DIR / "source_status.json", status)
    save_json(DATA_DIR / "request_state.json", requests)
    return finalize(gh, config, campaigns, cand_log, stats)


if __name__ == "__main__":
    setup_utf8_stdout()
    s = run()
    print(json.dumps(s, ensure_ascii=False, indent=2))
    sys.exit(0 if s["ok"] else 1)
