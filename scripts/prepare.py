"""【PC側・手順1】公式ページを取得し、前回から変わったページの「抽出依頼」を作る。

python scripts/prepare.py          … 通常
python scripts/prepare.py --force  … 変更がなくても全ページを依頼する

作るもの:
  inbox/<run_id>/queue.json   … 今回の依頼一覧（GitHub に送る）
  work/prompts/<source_id>.md … ChatGPT が読む依頼文（ページ本文入り。GitHub には送らない）
ChatGPT は依頼文ごとに inbox/<run_id>/<source_id>.json を書く。
"""
from __future__ import annotations

import json
import sys

from check_master import check_master
from collect import collect
from common import ROOT, WORK_DIR, load_config, load_master, load_sources, now_jst, save_json
from extract import build_prompt

INBOX = ROOT / "inbox"

ANSWER_NOTE = """

# 回答の書き方（この依頼文を読んでいる担当者へ）
- 回答は JSON だけを、次のファイルに保存する：{answer_file}
- 形は schema/llm_output.schema.json のとおり：{{"campaigns": [ ... ]}}。該当がなければ {{"campaigns": []}}
- すべての項目を必ず書く（わからない値は null、配列は []）
- 本文にないことは書かない。evidence_quote は本文からそのまま抜き出す（言い換えると、後の機械チェックで保留になる）
"""


def main(force: bool = False) -> dict:
    config = load_config()
    if not config.get("enabled", True):
        return {"ok": True, "message": "停止スイッチ（enabled: false）のため何もしない"}
    errors = check_master(load_master(), load_sources())
    if errors:
        return {"ok": False, "message": "マスタの形が壊れている", "errors": errors[:10]}

    now = now_jst()
    run_id = now.strftime("%Y%m%d-%H%M")
    run_dir = INBOX / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "prompts").mkdir(parents=True, exist_ok=True)
    limit = int(config.get("manual", {}).get("max_sources_per_run", 15))
    stores = load_master()["stores"]

    queue = {"run_id": run_id, "created_at": now.isoformat(timespec="seconds"),
             "items": [], "unchanged": [], "failed": [], "deferred": []}
    for page in collect(load_sources(), config, force=force, record_snapshots=False, record_status=False):
        sid = page.source["id"]
        if not page.ok:
            queue["failed"].append({"source_id": sid, "error": page.error})
            continue
        if not page.changed:
            queue["unchanged"].append(sid)
            continue
        if len(queue["items"]) >= limit:
            queue["deferred"].append(sid)  # 次回に回す（スナップショットを更新しないので自動で再依頼される）
            continue
        answer_file = f"inbox/{run_id}/{sid}.json"
        prompt_path = WORK_DIR / "prompts" / f"{sid}.md"
        prompt_path.write_text(build_prompt(page.source, page.text, stores, config)
                               + ANSWER_NOTE.format(answer_file=answer_file), encoding="utf-8")
        queue["items"].append({"source_id": sid, "hash": page.digest,
                               "prompt_file": f"work/prompts/{sid}.md", "answer_file": answer_file})

    save_json(run_dir / "queue.json", queue)
    return {"ok": True, "run_id": run_id, "to_answer": [i["prompt_file"] for i in queue["items"]],
            "unchanged": len(queue["unchanged"]), "failed": queue["failed"], "deferred": queue["deferred"]}


if __name__ == "__main__":
    res = main(force="--force" in sys.argv)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["ok"] else 1)
