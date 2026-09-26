"""日次処理の通し試験：取得・モデル・URL確認を偽物に差し替えて、リポジトリのコピー上で実行する。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

DRIVER = r'''
import json, sys
sys.path.insert(0, "scripts")
import collect, daily, validate

PAGE = "対象のお店でPayPay払いすると最大5%戻ってくる\n期間：2026年10月1日～2026年10月31日\n" \
       "抽選で最大全額戻ってくる 期間：2026年10月1日～2026年10月20日\n" \
       "【ご注意】2026年11月1日からチャージでのポイント付与を終了します"

class FakeFetcher(collect.Fetcher):
    def allowed(self, url): return True
    def get_static(self, url): return "<html><body><main>" + PAGE.replace("\n", "<br>") + "</main></body></html>"
    def get_js(self, url): return self.get_static(url)
collect.Fetcher = FakeFetcher

def fake_extract(source, text, stores, config):
    if source["id"] != "paypay-event":
        return []
    base = {"brand_ids": ["paypay"], "scope": {"kind": "payment_wide", "store_ids": [], "prefecture_codes": [], "municipality": None},
            "entry": {"required": False, "url": None}, "conditions": None, "official_url": "https://paypay.ne.jp/event/",
            "confidence": "confirmed"}
    return [
        dict(base, type="reward", title="対象のお店で最大5%戻ってくる",
             benefit={"rate_max": 5, "rate_text": "最大5%還元", "cap_per_use": None, "cap_total": None, "cap_unit": None},
             period={"start": "2026-10-01", "end": "2026-10-31"}, evidence_quote="対象のお店でPayPay払いすると最大5%戻ってくる"),
        dict(base, type="lottery", title="抽選で最大全額戻ってくる",
             benefit={"rate_max": None, "rate_text": "抽選で最大全額", "cap_per_use": None, "cap_total": None, "cap_unit": None},
             period={"start": "2026-10-01", "end": "2026-10-20"}, evidence_quote="抽選で最大全額戻ってくる 期間：2026年10月1日～2026年10月20日"),
        dict(base, type="change", title="チャージでのポイント付与終了",
             benefit={"rate_max": None, "rate_text": "付与終了", "cap_per_use": None, "cap_total": None, "cap_unit": None},
             period={"start": "2026-11-01", "end": "2026-12-31"}, evidence_quote="2026年11月1日からチャージでのポイント付与を終了します"),
        dict(base, type="reward", title="ページにない話",
             benefit={"rate_max": 3, "rate_text": "3%", "cap_per_use": None, "cap_total": None, "cap_unit": None},
             period={"start": "2026-10-01", "end": "2026-10-31"}, evidence_quote="この文はページにありません"),
    ]
daily.extract = fake_extract
daily.http_url_checker = lambda config: (lambda url, allowed: (True, ""))

first = daily.run()
second = daily.run()
print(json.dumps({"first": first, "second": second}, ensure_ascii=False))
'''


def test_daily_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "work", "__pycache__", ".pytest_cache"))
    for p in ["data/campaigns.json", "data/rejected.json"]:
        (repo / p).write_text("[]", encoding="utf-8")
    for p in ["data/holds.json", "data/snapshots.json", "data/source_status.json"]:
        (repo / p).write_text("{}", encoding="utf-8")
    (repo / "reports" / "runs.jsonl").unlink(missing_ok=True)
    srcs = repo / "config" / "sources.yaml"
    # この試験は一覧本文を使う。follow内にrender等があっても設定ブロック全体を外す。
    sources = yaml.safe_load(srcs.read_text(encoding="utf-8"))
    for source in sources:
        source.pop("follow", None)
    srcs.write_text(yaml.safe_dump(sources, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cfg = repo / "config" / "pipeline.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("provider: manual", "provider: anthropic"), encoding="utf-8")
    (repo / "driver.py").write_text(DRIVER, encoding="utf-8")
    # 親の環境を引き継ぎ（Windows で必要な SYSTEMROOT なども含む）、文字コードは UTF-8 に固定する
    env = {**os.environ, "POIKATSU_NOW": "2026-10-05T07:00:00+09:00", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "driver.py"], cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    first, second = res["first"], res["second"]

    # 1回目：5% と抽選は自動採用、改悪とページにない話は保留
    assert first["accepted_new"] == 2
    assert first["held"] == 2
    assert first["build"]["staging"] is True and first["build"]["live"] is False
    # 2回目：ページが変わっていないのでモデルは呼ばない
    assert second["llm_calls"] == 0 and second["pages_changed"] == 0

    bundle = json.loads((repo / "dist/staging/poikatsu.json").read_text(encoding="utf-8"))
    titles = sorted(c["title"] for c in bundle["campaigns"])
    assert titles == ["対象のお店で最大5%戻ってくる", "抽選で最大全額戻ってくる"]
    assert all("evidence_quote" not in c for c in bundle["campaigns"])
    holds_md = (repo / "reports/holds_local.md").read_text(encoding="utf-8")
    assert "V11" in holds_md and "V06" in holds_md
    assert (repo / "reports/run-2026-10-05.md").exists()
