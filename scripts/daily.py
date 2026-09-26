"""API方式の毎朝の実行（仕様書 §7）：取得 → 抽出（API）→ 機械チェック → 保留 → 配信ファイル作成 → 記録。

config/pipeline.yaml の llm.provider が "manual"（PCのChatGPTで抽出する方式）のときは何もしない。
その場合の流れは prepare.py（PC）→ ChatGPT が回答を書く → check_answers.py（PC）→ ingest.py（GitHub）。

python scripts/daily.py            … 通常
python scripts/daily.py --force    … 変更がなくても全ページを抽出し直す（手動実行用）
"""
from __future__ import annotations

import json
import sys

from collect import collect
from common import DATA_DIR, load_config, load_json, load_sources, save_json
from extract import extract
from holds import GitHub
from pipeline import finalize, new_stats, prepare_master, process_items
from validate import http_url_checker


def _forget_snapshot(source_id: str) -> None:
    """抽出できなかったページは、次回もう一度抽出されるようにハッシュを消す。"""
    snaps = load_json(DATA_DIR / "snapshots.json", {})
    if source_id in snaps:
        snaps[source_id].pop("hash", None)
        save_json(DATA_DIR / "snapshots.json", snaps)


def run(force: bool = False) -> dict:
    config = load_config()
    stats = new_stats("api", config)
    if config.get("llm", {}).get("provider") == "manual":
        stats["ok"] = True
        stats["errors"].append("抽出はPC側（manual）で行う設定のため、daily は何もしない")
        return stats
    if not stats["enabled"]:
        stats["ok"] = True
        stats["errors"].append("停止スイッチ（enabled: false）のため何もしていない")
        return stats

    gh = GitHub()
    master = prepare_master(gh, stats)
    if master is None:
        return stats

    campaigns = load_json(DATA_DIR / "campaigns.json", [])
    checker = http_url_checker(config)
    max_calls = int(config.get("llm", {}).get("max_llm_calls", 40))
    cand_log: list[dict] = []

    for page in collect(load_sources(), config, force=force):
        if not page.ok:
            stats["sources_failed"] += 1
            continue
        stats["sources_ok"] += 1
        if not page.changed:
            continue
        stats["pages_changed"] += 1
        src = page.source
        if stats["llm_calls"] >= max_calls:
            stats["llm_skipped"] += 1
            _forget_snapshot(src["id"])
            continue
        try:
            raw_items = extract(src, page.text, master["stores"], config)
            stats["llm_calls"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{src['id']}: 抽出失敗 {str(e)[:150]}")
            _forget_snapshot(src["id"])
            continue
        campaigns = process_items(raw_items, src, page.text, campaigns, master, config, gh, stats, cand_log, checker)

    return finalize(gh, config, campaigns, cand_log, stats)


if __name__ == "__main__":
    from common import setup_utf8_stdout

    setup_utf8_stdout()
    s = run(force="--force" in sys.argv)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    sys.exit(0 if s["ok"] else 1)
