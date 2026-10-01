"""保留の Issue 化と、承認（approve）・却下（reject）の取り込み（仕様書 §10）。

使い方（GitHub Actions から）:
  python scripts/holds.py process   … approve / reject ラベルを取り込み、7日放置に stale を付ける
GITHUB_TOKEN と GITHUB_REPOSITORY が無いときは、Issue の代わりに reports/holds_local.md に書く（試験用）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import timedelta

import requests

from campaign_shape import publication_errors
from common import DATA_DIR, REPORTS_DIR, load_config, load_json, now_jst, parse_dt, save_json, today_str

API = "https://api.github.com"
LABELS = {
    "hold": ("fbca04", "自動公開しなかった候補"),
    "approve": ("0e8a16", "公開してよい"),
    "reject": ("b60205", "公開しない"),
    "stale": ("c5def5", "7日以上放置"),
    "anomaly": ("d93f0b", "異常検知で本番更新を止めた"),
    "fetch-fail": ("5319e7", "取得元の連続失敗"),
}
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN")
        self.repo = os.environ.get("GITHUB_REPOSITORY")
        self.enabled = bool(self.token and self.repo)

    def _req(self, method: str, path: str, **kw):
        r = requests.request(
            method,
            f"{API}/repos/{self.repo}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"},
            timeout=30,
            **kw,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"GitHub API {method} {path}: {r.status_code} {r.text[:200]}")
        return r.json() if r.text else {}

    def ensure_labels(self) -> None:
        if not self.enabled:
            return
        existing = {l["name"] for l in self._req("GET", "/labels?per_page=100")}
        for name, (color, desc) in LABELS.items():
            if name not in existing:
                self._req("POST", "/labels", json={"name": name, "color": color, "description": desc})

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        if not self.enabled:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            with (REPORTS_DIR / "holds_local.md").open("a", encoding="utf-8") as f:
                f.write(f"\n\n## {title}\nlabels: {labels}\n\n{body}\n")
            return 0
        return self._req("POST", "/issues", json={"title": title, "body": body, "labels": labels})["number"]

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        if not self.enabled:
            return []
        issues = []
        page = 1
        while True:
            batch = self._req("GET", f"/issues?labels={label}&state={state}&per_page=100&page={page}")
            issues.extend(batch)
            if len(batch) < 100:
                return issues
            page += 1

    def comment(self, number: int, body: str) -> None:
        if self.enabled:
            self._req("POST", f"/issues/{number}/comments", json={"body": body})

    def close(self, number: int, body: str | None = None) -> None:
        if self.enabled:
            if body:
                self.comment(number, body)
            self._req("PATCH", f"/issues/{number}", json={"state": "closed"})

    def add_labels(self, number: int, labels: list[str]) -> None:
        if self.enabled:
            self._req("POST", f"/issues/{number}/labels", json={"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        if self.enabled:
            try:
                self._req("DELETE", f"/issues/{number}/labels/{label}")
            except RuntimeError:
                pass


def _field_table(c: dict) -> str:
    b, s, p, e = c.get("benefit", {}), c.get("scope", {}), c.get("period", {}), c.get("entry", {})
    required = e.get("required")
    entry_label = "必要" if required is True else "不要" if required is False else "不明"
    rows = [
        ("種類", c.get("type")), ("ブランド", ", ".join(c.get("brand_ids", []))), ("タイトル", c.get("title")),
        ("還元", f"{b.get('rate_text')}（最大 {b.get('rate_max')}%／1回上限 {b.get('cap_per_use')}／期間上限 {b.get('cap_total')} {b.get('cap_unit') or ''}）"),
        ("対象", f"{s.get('kind')} {', '.join(s.get('store_ids', []) + s.get('prefecture_codes', []))} {s.get('municipality') or ''}"),
        ("期間", f"{p.get('start') or '不明'} 〜 {p.get('end') or '不明'}"),
        ("エントリー", f"{entry_label} {e.get('url') or ''}"),
        ("条件", c.get("conditions")), ("公式URL", c.get("official_url")), ("確度", c.get("confidence")),
    ]
    return "\n".join(["| 項目 | 内容 |", "|---|---|"] + [f"| {k} | {str(v or '').replace('|', '／')} |" for k, v in rows])


def hold_body(c: dict, reasons: list[str], source: dict | None) -> str:
    return "\n".join([
        "## 保留の理由", *[f"- {r}" for r in reasons], "",
        "## 内容", _field_table(c), "",
        "## 公式ページの根拠文", f"> {c.get('evidence_quote', '')}", "",
        f"取得元：{(source or {}).get('name', c.get('source_id'))}（{(source or {}).get('url', '')}）", "",
        "## 操作",
        "- 公開してよい → ラベル **approve** を付ける",
        "- 公開しない → ラベル **reject** を付ける",
        "- 直してから公開 → 下の JSON を編集して保存し、ラベル **approve** を付ける", "",
        "<!-- candidate -->",
        "```json",
        json.dumps(c, ensure_ascii=False, indent=2),
        "```",
    ])


def file_hold(gh: GitHub, c: dict, reasons: list[str], source: dict | None) -> bool:
    """保留を1件登録する。既に同じIDが保留中・却下済みなら何もしない。"""
    holds = load_json(DATA_DIR / "holds.json", {})
    rejected = set(load_json(DATA_DIR / "rejected.json", []))
    if c["id"] in holds or c["id"] in rejected:
        return False
    number = gh.create_issue(f"[保留] {c.get('title', '')}", hold_body(c, reasons, source), ["hold"])
    holds[c["id"]] = {"issue": number, "created": today_str(), "reasons": reasons}
    save_json(DATA_DIR / "holds.json", holds)
    return True


def _same_published_content(candidate: dict, adopted: dict) -> bool:
    """ID だけでは内容は一致しない。配信項目が全て同じときだけ解決済みにする。"""
    if publication_errors(candidate) or publication_errors(adopted):
        return False
    fields = ("type", "brand_ids", "title", "benefit", "scope", "period", "entry",
              "conditions", "official_url")
    return candidate.get("id") == adopted.get("id") and all(
        candidate[key] == adopted[key] for key in fields
    )


def process() -> dict:
    """approve / reject を取り込み、放置に stale を付ける。"""
    from candidates import apply_update, match_existing
    from common import load_master, load_sources
    from validate import Context, http_url_checker, validate_candidate

    config = load_config()
    gh = GitHub()
    gh.ensure_labels()
    master = load_master()
    brands = {b["id"]: b for b in master["brands"]}
    stores = {s["id"]: s for s in master["stores"]}
    sources = {s["id"]: s for s in load_sources()}
    campaigns = load_json(DATA_DIR / "campaigns.json", [])
    holds = load_json(DATA_DIR / "holds.json", {})
    rejected = load_json(DATA_DIR / "rejected.json", [])
    result = {"approved": 0, "rejected": 0, "failed": 0, "stale": 0, "expired": 0, "resolved": 0}

    for issue in gh.list_issues("hold"):
        labels = {l["name"] for l in issue.get("labels", [])}
        number = issue["number"]
        m = JSON_BLOCK.search(issue.get("body") or "")
        cand = json.loads(m.group(1)) if m else None
        cid = (cand or {}).get("id")

        if "reject" in labels:
            if cid and cid not in rejected:
                rejected.append(cid)
            holds.pop(cid, None)
            gh.close(number, "却下として記録しました。同じ候補は今後保留にしません。")
            result["rejected"] += 1
            continue

        if "approve" in labels:
            if not cand:
                gh.comment(number, "JSON が読み取れませんでした。```json ブロックを確認してください。")
                gh.remove_label(number, "approve")
                result["failed"] += 1
                continue
            ctx = Context(
                brands=brands, stores=stores, source=sources.get(cand.get("source_id")), page_text=None,
                config=config, url_checker=http_url_checker(config),
                only={"V01", "V02", "V03", "V04", "V05", "V08", "V09"},
            )
            decision, reasons = validate_candidate(cand, ctx)
            if decision != "accept":
                gh.comment(number, "再チェックで問題がありました：\n" + "\n".join(f"- {r}" for r in reasons))
                gh.remove_label(number, "approve")
                result["failed"] += 1
                continue
            cand["last_verified"] = today_str()
            kind, ex = match_existing(cand, campaigns)
            if kind == "new":
                campaigns.append(cand)
            else:
                campaigns = [apply_update(ex, cand) if x is ex else x for x in campaigns]
            holds.pop(cid, None)
            gh.close(number, "承認を反映しました。次の配信ファイル更新から表示されます。")
            result["approved"] += 1
            continue

        # 取り直しなどで同じ内容が既に採用済みなら、保留は役目を終えたので閉じる
        if cid and any(_same_published_content(cand, x) for x in campaigns):
            holds.pop(cid, None)
            gh.close(number, "同じ内容が機械チェックを通って採用済みのため、自動で閉じました。")
            result["resolved"] += 1
            continue

        # 終了日を過ぎた保留は、もう公開できないので自動で閉じる（採用も却下もしない。rejected には入れない）
        end = ((cand or {}).get("period") or {}).get("end")
        try:
            ended = bool(end) and parse_dt(end) < now_jst()
        except ValueError:
            ended = False
        if ended:
            holds.pop(cid, None)
            gh.close(number, "終了日を過ぎたため自動で閉じました（採用も却下もしていません）。")
            result["expired"] += 1
            continue

        created = parse_dt(issue["created_at"].replace("Z", "+00:00"))
        if now_jst() - created > timedelta(days=7) and "stale" not in labels:
            gh.add_labels(number, ["stale"])
            result["stale"] += 1

    save_json(DATA_DIR / "campaigns.json", campaigns)
    save_json(DATA_DIR / "holds.json", holds)
    save_json(DATA_DIR / "rejected.json", rejected)
    return result


if __name__ == "__main__":
    from common import setup_utf8_stdout

    setup_utf8_stdout()
    if len(sys.argv) > 1 and sys.argv[1] == "process":
        res = process()
        print(json.dumps(res, ensure_ascii=False))
        from build import build
        print(json.dumps(build(), ensure_ascii=False))
    else:
        print("usage: python scripts/holds.py process")
