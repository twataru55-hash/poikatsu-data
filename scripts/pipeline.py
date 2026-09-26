"""日次処理（API方式 daily.py）と取り込み（PC抽出方式 ingest.py）で共通の処理。"""
from __future__ import annotations

import json

from build import build
from candidates import apply_update, match_existing, normalize
from check_master import check_master, needs_verify, verify_online
from common import (
    DATA_DIR,
    REPORTS_DIR,
    append_run_log,
    load_json,
    load_master,
    load_sources,
    now_jst,
    save_json,
    today_str,
)
from holds import GitHub, file_hold
from validate import Context, validate_candidate


def new_stats(mode: str, config: dict) -> dict:
    return {
        "date": now_jst().isoformat(timespec="seconds"), "mode": mode, "ok": False,
        "enabled": bool(config.get("enabled", True)),
        "sources_ok": 0, "sources_failed": 0, "pages_changed": 0, "llm_calls": 0, "llm_skipped": 0,
        "candidates": 0, "same": 0, "accepted_new": 0, "accepted_updated": 0, "held": 0, "discarded": 0,
        "errors": [], "build": None,
    }


def prepare_master(gh: GitHub, stats: dict) -> dict | None:
    """マスタの形を確かめ、未確認のものは公式ページで根拠を確かめる。壊れていれば None。"""
    master = load_master()
    errors = check_master(master, load_sources())
    gh.ensure_labels()
    if errors:
        gh.create_issue("[異常] マスタデータの形が壊れています", "\n".join(f"- {e}" for e in errors), ["anomaly"])
        stats["errors"] += errors[:10]
        return None
    if needs_verify():
        stats["master_verify_failures"] = verify_online()
        master = load_master()
    return master


def process_items(raw_items: list, src: dict, page_text: str, campaigns: list[dict], master: dict,
                  config: dict, gh: GitHub, stats: dict, cand_log: list[dict], url_checker) -> list[dict]:
    """1ページ分の抽出結果を、整形 → 突き合わせ → 機械チェック → 採用／保留 する。"""
    brands = {b["id"]: b for b in master["brands"]}
    stores = {s["id"]: s for s in master["stores"]}
    ctx = Context(brands=brands, stores=stores, source=src, page_text=page_text, config=config, url_checker=url_checker)
    for raw in raw_items:
        if not isinstance(raw, dict):
            stats["discarded"] += 1
            continue
        stats["candidates"] += 1
        c = normalize(raw, src)
        kind, existing = match_existing(c, campaigns)
        if kind == "same":
            existing["last_verified"] = today_str()
            stats["same"] += 1
            cand_log.append({"id": existing["id"], "result": "same", "source": src["id"]})
            continue
        decision, reasons = validate_candidate(c, ctx)
        cand_log.append({"id": c["id"], "result": decision, "match": kind, "reasons": reasons,
                         "source": src["id"], "title": c.get("title")})
        if decision == "discard":
            stats["discarded"] += 1
        elif decision == "hold":
            if file_hold(gh, c, reasons, src):
                stats["held"] += 1
        elif kind == "new":
            campaigns.append(c)
            stats["accepted_new"] += 1
        else:
            campaigns = [apply_update(existing, c) if x is existing else x for x in campaigns]
            stats["accepted_updated"] += 1
    return campaigns


def finalize(gh: GitHub, config: dict, campaigns: list[dict], cand_log: list[dict], stats: dict) -> dict:
    """保存・取得失敗の通知・配信ファイル作成・記録。"""
    save_json(DATA_DIR / "campaigns.json", campaigns)
    if cand_log:
        path = DATA_DIR / "candidates" / f"{today_str()}.json"
        save_json(path, load_json(path, []) + cand_log)

    status = load_json(DATA_DIR / "source_status.json", {})
    limit = int(config.get("fetch", {}).get("fail_issue_after", 3))
    for sid, st in status.items():
        if st.get("fail_count", 0) >= limit and not st.get("issue_open"):
            gh.create_issue(
                f"[取得失敗] {sid}",
                f"{st.get('fail_count')} 回連続で取得できませんでした。\n\n最後のエラー：{st.get('last_error')}\n\nURL が変わっていないか確認してください。",
                ["fetch-fail"],
            )
            st["issue_open"] = True
        elif st.get("fail_count", 0) == 0 and st.get("issue_open"):
            st["issue_open"] = False
    save_json(DATA_DIR / "source_status.json", status)

    result = build()
    stats["build"] = result
    if result.get("anomaly"):
        gh.create_issue(
            "[異常] 本番データの更新を止めました",
            "\n".join(f"- {p}" for p in result["anomaly"])
            + "\n\n確認して問題なければ、config/pipeline.yaml の anomaly の値を見直すか、次回の実行を待ってください。",
            ["anomaly"],
        )
    if result.get("error"):
        stats["errors"].append(result["error"])
    stats["ok"] = not result.get("error")
    append_run_log(stats)
    write_report(stats, cand_log)
    return stats


def write_report(stats: dict, cand_log: list[dict]) -> None:
    keys = ("sources_ok", "sources_failed", "pages_changed", "llm_calls", "llm_skipped",
            "candidates", "same", "accepted_new", "accepted_updated", "held", "discarded")
    lines = [
        f"# 実行結果 {stats['date']}（{stats.get('mode')}）", "",
        "| 項目 | 件数 |", "|---|---|",
        *[f"| {k} | {stats.get(k)} |" for k in keys],
        "", f"配信：{json.dumps(stats.get('build'), ensure_ascii=False)}", "",
    ]
    if stats.get("master_verify_failures"):
        lines += ["## マスタの根拠確認で見つからなかったもの", *[f"- {m}" for m in stats["master_verify_failures"]], ""]
    if stats["errors"]:
        lines += ["## エラー", *[f"- {e}" for e in stats["errors"]], ""]
    held = [c for c in cand_log if c.get("result") in ("hold", "discard")]
    if held:
        lines += ["## 保留・破棄", *[f"- [{c['result']}] {c.get('title')}：{' / '.join(c.get('reasons') or [])}" for c in held]]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"run-{today_str()}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
