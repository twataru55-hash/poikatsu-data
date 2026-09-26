"""master/ と config/sources.yaml の形と参照関係をチェックする。

python scripts/check_master.py          … 形と参照関係だけ（オフライン）
python scripts/check_master.py --online … 根拠文が公式ページに実在するかも確認し、verified を更新
"""
from __future__ import annotations

import json
import sys

from jsonschema import Draft202012Validator

from common import MASTER_DIR, SCHEMA_DIR, load_json, load_master, load_sources, normalize_text, save_json, today_str


def _schema_errors(name: str, data) -> list[str]:
    v = Draft202012Validator(load_json(SCHEMA_DIR / f"{name}.schema.json"))
    return [f"{name}: {'/'.join(map(str, e.path)) or '(root)'} {e.message}" for e in v.iter_errors(data)][:20]


def check_master(master: dict | None = None, sources: list | None = None) -> list[str]:
    master = master or load_master()
    sources = sources if sources is not None else load_sources()
    errors: list[str] = []
    for name in ("brands", "stores", "recurring", "phrases", "pr_links"):
        errors += _schema_errors(name, master[name])
    errors += _schema_errors("sources", sources)
    if errors:
        return errors

    brand_ids = [b["id"] for b in master["brands"]]
    store_ids = [s["id"] for s in master["stores"]]
    for label, ids in (("brands", brand_ids), ("stores", store_ids), ("sources", [s["id"] for s in sources]),
                       ("recurring", [r["id"] for r in master["recurring"]])):
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            errors.append(f"{label}: IDが重複 {sorted(dup)}")
    bset, sset = set(brand_ids), set(store_ids)
    for b in master["brands"]:
        if b["point_id"] not in bset:
            errors.append(f"brands/{b['id']}: point_id {b['point_id']} が未登録")
    for s in master["stores"]:
        for b in s["accepted_brand_ids"]:
            if b not in bset:
                errors.append(f"stores/{s['id']}: accepted_brand_ids に未登録 {b}")
        for r in s["base_rewards"]:
            if r["brand_id"] not in bset:
                errors.append(f"stores/{s['id']}: base_rewards に未登録 {r['brand_id']}")
    for r in master["recurring"]:
        for s in r["store_ids"]:
            if s not in sset:
                errors.append(f"recurring/{r['id']}: 未登録の店 {s}")
        for b in r["brand_ids"]:
            if b not in bset:
                errors.append(f"recurring/{r['id']}: 未登録のブランド {b}")
    for p in master["pr_links"]:
        if p["brand_id"] not in bset:
            errors.append(f"pr_links: 未登録のブランド {p['brand_id']}")
    for src in sources:
        for b in src.get("brand_ids", []):
            if b not in bset:
                errors.append(f"sources/{src['id']}: 未登録のブランド {b}")
        for s in src.get("store_ids", []) or []:
            if s not in sset:
                errors.append(f"sources/{src['id']}: 未登録の店 {s}")
    return errors


def needs_verify() -> bool:
    """まだ確認していない（verified が true でない）マスタがあるか。"""
    for s in load_json(MASTER_DIR / "stores.json", []):
        if s.get("accepted_source") and s["accepted_source"].get("verified") is not True:
            return True
        if any(r.get("verified") is not True for r in s.get("base_rewards", [])):
            return True
    return any(r.get("verified") is not True for r in load_json(MASTER_DIR / "recurring.json", []))


def verify_online() -> list[str]:
    """master の根拠文が公式ページに実在するかを確かめ、verified を書き換える。失敗の一覧を返す。"""
    from collect import Fetcher, html_to_text
    from common import load_config

    fetcher = Fetcher(load_config())
    cache: dict[str, str] = {}
    failures: list[str] = []

    def page(url: str) -> str:
        if url not in cache:
            try:
                cache[url] = normalize_text(html_to_text(fetcher.get_static(url)))
            except Exception as e:  # noqa: BLE001
                cache[url] = ""
                failures.append(f"取得失敗 {url}: {e}")
        return cache[url]

    def check(item: dict, label: str) -> None:
        ok = bool(item.get("evidence_quote")) and normalize_text(item["evidence_quote"]) in page(item["official_url"])
        item["verified"] = ok
        if ok:
            item["last_verified"] = today_str()
        else:
            failures.append(f"{label}: 根拠文が見つからない（{item['official_url']}）")

    brands = {b["id"]: b for b in load_json(MASTER_DIR / "brands.json", [])}
    stores = load_json(MASTER_DIR / "stores.json", [])
    for s in stores:
        src = s.get("accepted_source")
        if src:
            # 使える決済は「ブランドごと」に確かめる。見つかったものだけ配信し、見つからないものは報告する
            text = page(src["official_url"])
            found = [
                b for b in s.get("accepted_brand_ids", [])
                if text and any(normalize_text(t) in text for t in (brands.get(b) or {}).get("match_terms", []))
            ]
            missing = [b for b in s.get("accepted_brand_ids", []) if b not in found]
            src["verified_brand_ids"] = found
            src["verified"] = bool(found)
            if found:
                src["last_verified"] = today_str()
            if missing:
                failures.append(f"stores/{s['id']}/accepted_source: 支払い方法ページに見つからない {missing}（この決済は配信しない）")
        for r in s.get("base_rewards", []):
            check(r, f"stores/{s['id']}/base_rewards/{r['brand_id']}")
    recurring = load_json(MASTER_DIR / "recurring.json", [])
    for r in recurring:
        check(r, f"recurring/{r['id']}")
    save_json(MASTER_DIR / "stores.json", stores)
    save_json(MASTER_DIR / "recurring.json", recurring)
    return failures


if __name__ == "__main__":
    from common import setup_utf8_stdout

    setup_utf8_stdout()
    errs = check_master()
    print(json.dumps({"errors": errs}, ensure_ascii=False, indent=2))
    if "--online" in sys.argv and not errs:
        print(json.dumps({"verify_failures": verify_online()}, ensure_ascii=False, indent=2))
    sys.exit(1 if errs else 0)
