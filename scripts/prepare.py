"""【PC側・手順1】公式ページを取得し、前回から変わったページの「抽出依頼」を作る。

python scripts/prepare.py          … 通常
python scripts/prepare.py --force  … 変更がなくても全ページを依頼する

作るもの:
  inbox/<run_id>/queue.json   … 今回の依頼一覧（GitHub に送る）
  work/prompts/<source_id>.md … ChatGPT が読む依頼文（ページ本文入り。GitHub には送らない）
ChatGPT は依頼文ごとに inbox/<run_id>/<source_id>.json を書く。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import argparse

from check_master import check_master
from collect import Fetcher, collect, extract_links, html_to_text
from common import DATA_DIR, ROOT, WORK_DIR, domain_allowed, load_config, load_json, load_master, load_sources, now_jst, save_json, setup_utf8_stdout
from extract import build_prompt

INBOX = ROOT / "inbox"

ANSWER_NOTE = """

# 回答の書き方（この依頼文を読んでいる担当者へ）
- 回答は JSON だけを、次のファイルに保存する：{answer_file}
- 形は schema/llm_output.schema.json のとおり：{{"campaigns": [ ... ]}}。該当がなければ {{"campaigns": []}}
- すべての項目を必ず書く（わからない値は null、配列は []）
- 本文にないことは書かない。evidence_quote は本文からそのまま抜き出す（言い換えると、後の機械チェックで保留になる）
"""


LINK_WORDS = ("エントリー", "参加", "応募", "登録", "申し込", "申込", "詳しく", "詳細", "対象店舗", "対象のお店")


def link_section(links: list) -> str:
    """詳細ページのリンクのうち、エントリー先などを探すのに役立つものを本文の後ろに付ける。"""
    picked = [(t, u) for t, u in links if any(w in t for w in LINK_WORDS)][:20]
    if not picked:
        return ""
    return "\n\n# ページ内のリンク（エントリー先・対象店舗の確認用）\n" + "\n".join(f"- {t}：{u}" for t, u in picked)


def detail_source(parent: dict, url: str) -> dict:
    """一覧ページ（parent）からたどった詳細ページを、取得元として扱える形にする。"""
    did = f"{parent['id']}--{hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]}"
    follow = parent.get("follow") or {}
    return {**parent, "id": did, "url": url, "name": f"{parent['name']}（詳細ページ）",
            "render": follow.get("render", parent.get("render", "static")), "parent": parent["id"]}


def follow_details(page, config: dict, snapshots: dict, fetcher: Fetcher, force=False, audit=None) -> tuple[list, list]:
    """一覧ページのリンクから、まだ処理していない詳細ページを取得する。
    戻り値：(取得できた [(src, text, digest)], 失敗 [{source_id, url, error}])"""
    from discovery import discover_and_refresh
    return discover_and_refresh(page, config, snapshots, fetcher, force, audit)


def fair_order(pending: list, state: dict) -> list:
    """依頼の順番を決める。取得元（一覧ページ単位）ごとに1件ずつ順番に取り、
    同じ取得元の中では、前回までに後回しにされた古いものから先に出す。
    （上限で毎回同じ取得元が後回しになり、ずっと処理されない状態を防ぐ）"""
    groups: dict[str, list] = {}
    for entry in pending:
        groups.setdefault(entry[3], []).append(entry)
    for key in groups:
        groups[key].sort(key=lambda e: (state.get(e[0]["id"], {}).get("deferred_since") or "9999"))
    # 後回しが一番古い取得元から回す
    order = sorted(groups, key=lambda k: min(state.get(e[0]["id"], {}).get("deferred_since") or "9999" for e in groups[k]))
    result, i = [], 0
    while any(i < len(groups[k]) for k in order):
        for k in order:
            if i < len(groups[k]):
                result.append(groups[k][i])
        i += 1
    return result


def main(force: bool = False, limit_override: int | None = None) -> dict:
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
    limit = limit_override or int(config.get("manual", {}).get("max_sources_per_run", 40))
    stores = load_master()["stores"]

    queue = {"run_id": run_id, "created_at": now.isoformat(timespec="seconds"),
             "items": [], "unchanged": [], "failed": [], "deferred": [], "detail_failed": [], "gave_up": [], "coverage": []}
    snapshots = load_json(DATA_DIR / "snapshots.json", {})
    # GitHub 側（ingest）が記録する依頼の状態：後回しにされた日時・回答されなかった回数
    state = load_json(DATA_DIR / "request_state.json", {})
    give_up = int(config.get("manual", {}).get("give_up_after_unanswered", 3))
    pending: list = []
    fetcher = Fetcher(config)

    def queue_up(src: dict, text: str, digest: str) -> None:
        sid = src["id"]
        st = state.get(sid) or {}
        if not force and st.get("unanswered", 0) >= give_up and st.get("hash") == digest:
            # 同じ内容のページが続けて回答されていない。ページが変わるまで依頼しない（報告には出す）
            queue["gave_up"].append(sid)
            return
        pending.append((src, text, digest, src.get("parent") or sid))

    def add_item(src: dict, text: str, digest: str) -> None:
        sid = src["id"]
        max_chars = int(config.get("llm", {}).get("max_input_chars", 30000))
        if len(text) > max_chars:
            queue["detail_failed"].append({"source_id": sid, "url": src["url"], "error": f"本文{len(text)}字が上限{max_chars}字を超過。黙って切り捨てず未回答。"})
            return
        if len(queue["items"]) >= limit:
            queue["deferred"].append(sid)  # 次回に回す（スナップショットを更新しないので自動で再依頼される）
            return
        answer_file = f"inbox/{run_id}/{sid}.json"
        (WORK_DIR / "prompts" / f"{sid}.md").write_text(
            build_prompt(src, text, stores, config) + ANSWER_NOTE.format(answer_file=answer_file), encoding="utf-8")
        item = {"source_id": sid, "hash": digest, "prompt_file": f"work/prompts/{sid}.md", "answer_file": answer_file}
        if src.get("parent"):
            item.update(parent=src["parent"], url=src["url"])
        queue["items"].append(item)

    for page in collect(load_sources(), config, force=force, record_snapshots=False, record_status=False):
        sid = page.source["id"]
        if not page.ok:
            queue["failed"].append({"source_id": sid, "error": page.error})
            continue
        if page.source.get("follow"):
            # 一覧ページは「詳細ページを見つける」ためだけに使い、詳細ページを1件ずつ依頼する
            audit = {}
            got, failed = follow_details(page, config, snapshots, fetcher, force, audit)
            queue["coverage"].append(audit)
            queue["unchanged"].extend(audit.get("unchanged", []))
            queue["detail_failed"] += failed
            for src, text, digest in got:
                queue_up(src, text, digest)
            continue
        if not page.changed:
            queue["unchanged"].append(sid)
            continue
        queue_up(page.source, page.text, page.digest)

    for src, text, digest, _group in fair_order(pending, state):
        add_item(src, text, digest)

    save_json(run_dir / "queue.json", queue)
    save_json(WORK_DIR / "discovery" / "latest-coverage.json", {"run_id": run_id, "sources": queue["coverage"], "deferred": queue["deferred"], "failed": queue["failed"], "detail_failed": queue["detail_failed"]})
    return {"ok": True, "run_id": run_id, "to_answer": [i["prompt_file"] for i in queue["items"]],
            "unchanged": len(queue["unchanged"]), "failed": queue["failed"], "deferred": queue["deferred"],
            "detail_failed": queue["detail_failed"], "gave_up": queue["gave_up"]}


if __name__ == "__main__":
    setup_utf8_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= 300:
        parser.error("--limit は1〜300")
    res = main(force=args.force, limit_override=args.limit)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["ok"] else 1)
