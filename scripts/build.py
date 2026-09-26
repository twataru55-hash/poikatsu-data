"""配信ファイル dist/*/poikatsu.json を作る（仕様書 §5-7・§11・§12）。

- staging は毎回更新
- live は publish_to_live: true のときだけ。異常検知に引っかかったら更新しない
- live を更新するときは、直前の版を dist/live/history に残す（最大 keep_history 版）
- 終了して archive_after_days 経ったキャンペーンは data/archive/YYYY-MM.json へ移す
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import timedelta

from common import (
    DATA_DIR,
    DIST_DIR,
    campaign_status,
    is_live,
    load_config,
    load_json,
    load_master,
    now_jst,
    parse_dt,
    save_json,
)

MAX_BYTES = 2 * 1024 * 1024


def archive_old(campaigns: list[dict], days: int) -> list[dict]:
    now = now_jst()
    keep, moved = [], {}
    for c in campaigns:
        end = parse_dt(c["period"]["end"])
        if end < now - timedelta(days=days):
            moved.setdefault(end.strftime("%Y-%m"), []).append(c)
        else:
            keep.append(c)
    for ym, items in moved.items():
        path = DATA_DIR / "archive" / f"{ym}.json"
        existing = load_json(path, [])
        ids = {x["id"] for x in existing}
        save_json(path, existing + [x for x in items if x["id"] not in ids])
    return keep


def make_bundle(campaigns: list[dict], master: dict, config: dict, channel: str) -> dict:
    now = now_jst()
    within = now + timedelta(days=int(config.get("bundle", {}).get("upcoming_within_days", 30)))
    selected = []
    for c in campaigns:
        st = campaign_status(c, now)
        if st == "ended":
            continue
        if parse_dt(c["period"]["start"]) > within:
            continue
        item = {k: v for k, v in c.items() if k not in ("evidence_quote", "source_id", "first_seen")}
        selected.append(item)
    selected.sort(key=lambda c: (c["period"]["end"], c["id"]))
    # 常設の還元・定例日は、根拠文を機械で確認できたもの（verified: true）だけ配信する
    stores = [
        {**s, "base_rewards": [
            {k: v for k, v in r.items() if k not in ("evidence_quote", "verified")}
            for r in s.get("base_rewards", []) if r.get("verified") is True
        ]}
        for s in master["stores"]
    ]
    for s in stores:
        # 使える決済は、支払い方法ページで機械確認できたときだけ配信する
        if (s.get("accepted_source") or {}).get("verified") is not True:
            s["accepted_brand_ids"] = []
        s.pop("accepted_source", None)
        s.pop("domains", None)
    recurring = [
        {k: v for k, v in r.items() if k not in ("evidence_quote", "verified")}
        for r in master["recurring"] if r.get("verified") is True
    ]
    counts = {
        "campaigns": len(selected),
        "live_campaigns": sum(1 for c in selected if is_live(campaign_status(c, now))),
        "stores": len(stores),
        "recurring": len(recurring),
    }
    return {
        "meta": {
            "schema_version": 1,
            "data_version": now.strftime("%Y-%m-%d-%H%M"),
            "generated_at": now.isoformat(timespec="seconds"),
            "channel": channel,
            "counts": counts,
        },
        "brands": master["brands"],
        "stores": stores,
        "recurring": recurring,
        "campaigns": selected,
        "pr_links": [p for p in master["pr_links"] if p.get("active")],
        "phrases": master["phrases"],
    }


def detect_anomaly(new: dict, old: dict | None, config: dict) -> list[str]:
    """前回の live と比べて、件数が急に変わっていないか。"""
    if not old:
        return []
    a = config.get("anomaly", {})
    now = now_jst()

    def live_ids(bundle):
        return {c["id"] for c in bundle.get("campaigns", []) if is_live(campaign_status(c, now))}

    old_ids, new_ids = live_ids(old), live_ids(new)
    problems = []
    if len(old_ids) >= int(a.get("min_items_for_ratio", 10)):
        ratio = abs(len(new_ids) - len(old_ids)) / len(old_ids)
        if ratio > float(a.get("max_change_ratio", 0.3)):
            problems.append(f"実施中の件数が {len(old_ids)} → {len(new_ids)} に急変（{ratio:.0%}）")
    old_by_id = {c["id"]: c for c in old.get("campaigns", [])}
    removed = [
        i for i in old_ids - new_ids
        if campaign_status(old_by_id[i], now) != "ended"  # 自然に終わったものは数えない
    ]
    if len(removed) > int(a.get("max_removed_per_run", 10)):
        problems.append(f"終了前のキャンペーンが {len(removed)} 件消えた")
    return problems


def build() -> dict:
    config = load_config()
    master = load_master()
    campaigns = load_json(DATA_DIR / "campaigns.json", [])
    campaigns = archive_old(campaigns, int(config.get("bundle", {}).get("archive_after_days", 7)))
    save_json(DATA_DIR / "campaigns.json", campaigns)

    result = {"staging": False, "live": False, "anomaly": []}
    staging = make_bundle(campaigns, master, config, "staging")
    if len(json.dumps(staging, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        result["error"] = "配信ファイルが2MBを超えた"
        return result
    save_json(DIST_DIR / "staging" / "poikatsu.json", staging)
    result["staging"] = True

    if not config.get("publish_to_live"):
        return result

    live_path = DIST_DIR / "live" / "poikatsu.json"
    old = load_json(live_path, None)
    live = make_bundle(campaigns, master, config, "live")
    problems = detect_anomaly(live, old, config)
    if problems:
        result["anomaly"] = problems
        return result

    if old:
        hist_dir = DIST_DIR / "live" / "history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(live_path, hist_dir / f"{old['meta']['data_version']}.json")
        files = sorted(p for p in hist_dir.glob("*.json"))
        keep = int(config.get("bundle", {}).get("keep_history", 7))
        for p in files[:-keep] if len(files) > keep else []:
            p.unlink()
    save_json(live_path, live)
    result["live"] = True
    return result


if __name__ == "__main__":
    res = build()
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(1 if res.get("error") else 0)
