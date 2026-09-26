"""【PC側・手順3】ChatGPT が書いた回答ファイルの形をチェックする。

python scripts/check_answers.py
  最新の inbox/<run_id>/ を調べ、問題があれば一覧を出して終了コード1を返す。
  回答が無いページは「次回に再依頼」になるだけなので、エラーにはしない。
"""
from __future__ import annotations

import json
import sys

from jsonschema import Draft202012Validator

from common import ROOT, SCHEMA_DIR, load_json, load_sources

INBOX = ROOT / "inbox"


def latest_run():
    runs = sorted(p for p in INBOX.iterdir() if p.is_dir() and (p / "queue.json").exists()) if INBOX.exists() else []
    return runs[-1] if runs else None


def main() -> dict:
    run = latest_run()
    if not run:
        return {"ok": False, "problems": ["inbox に依頼がない（先に prepare.py を実行する）"]}
    queue = load_json(run / "queue.json", {})
    schema = load_json(SCHEMA_DIR / "llm_output.schema.json")
    validator = Draft202012Validator(schema)
    sources = {s["id"]: s for s in load_sources()}
    problems, answered, missing = [], [], []
    for item in queue.get("items", []):
        path = ROOT / item["answer_file"]
        if not path.exists():
            missing.append(item["source_id"])
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{item['answer_file']}: JSONとして読めない（{e}）")
            continue
        for err in list(validator.iter_errors(data))[:5]:
            where = "/".join(map(str, err.path)) or "(root)"
            problems.append(f"{item['answer_file']}: {where} {err.message}")
        allowed = set(sources.get(item["source_id"], {}).get("brand_ids", []))
        for i, c in enumerate(data.get("campaigns", []) if isinstance(data, dict) else []):
            extra = set(c.get("brand_ids", [])) - allowed
            if allowed and extra:
                problems.append(f"{item['answer_file']}: campaigns/{i}/brand_ids に使えない値 {sorted(extra)}")
            if not (c.get("evidence_quote") or "").strip():
                problems.append(f"{item['answer_file']}: campaigns/{i}/evidence_quote が空")
        answered.append(item["source_id"])
    return {"ok": not problems, "run_id": run.name, "answered": answered, "missing": missing, "problems": problems}


if __name__ == "__main__":
    res = main()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["ok"] else 1)
