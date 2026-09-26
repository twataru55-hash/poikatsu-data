"""PC抽出方式（manual）の通し試験：prepare → 回答を書く → check_answers → ingest。
一覧ページから詳細ページをたどる（follow）流れも確かめる。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FAKE = r'''
import sys
sys.path.insert(0, "scripts")
import collect, validate
LOCAL = "対象のお店でPayPay払いすると最大5%戻ってくる\n期間：2026年10月1日～2026年10月31日"
DETAIL = "コンビニで最大10%戻ってくるキャンペーン\n期間：2026年10月1日～2026年10月20日\n要エントリー"
def page(body, links=""):
    return "<html><body><main>" + body.replace("\n", "<br>") + links + "</main></body></html>"
class FakeFetcher(collect.Fetcher):
    def allowed(self, url): return True
    def get_static(self, url):
        if url == "https://paypay.ne.jp/event/":
            return page("キャンペーン一覧です。いろいろあります。いろいろあります。いろいろあります。",
                        '<a href="/event/test-20261001/">コンビニ10%</a><a href="/event/kisekae-x/">きせかえ</a>')
        if url == "https://paypay.ne.jp/event/test-20261001/":
            return page(DETAIL, '<a href="https://paypay.ne.jp/entry/test/">エントリーする</a>')
        return page(LOCAL)
    def get_js(self, url): return self.get_static(url)
collect.Fetcher = FakeFetcher
validate.http_url_checker = lambda config: (lambda url, allowed: (True, ""))
'''

PREPARE = FAKE + r'''
import json, prepare
print(json.dumps(prepare.main(), ensure_ascii=False))
'''

ANSWER = r'''
import json, sys
from pathlib import Path
q = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
base = {"scope": {"kind": "payment_wide", "store_ids": [], "prefecture_codes": [], "municipality": None},
        "conditions": None, "confidence": "confirmed", "brand_ids": ["paypay"]}
for item in q["items"]:
    if item["source_id"] == "paypay-local":
        ans = {"campaigns": [dict(base, type="reward", title="対象のお店で最大5%戻ってくる",
            benefit={"rate_max": 5, "rate_text": "最大5%還元", "cap_per_use": None, "cap_total": None, "cap_unit": None},
            period={"start": "2026-10-01T00:00:00+09:00", "end": "2026-10-31T23:59:59+09:00"},
            entry={"required": False, "url": None},
            official_url="https://paypay.ne.jp/event/support-local/", evidence_quote="対象のお店でPayPay払いすると最大5%戻ってくる")]}
    elif item.get("parent") == "paypay-event":
        ans = {"campaigns": [dict(base, type="reward", title="コンビニで最大10%戻ってくる",
            benefit={"rate_max": 10, "rate_text": "最大10%還元", "cap_per_use": None, "cap_total": None, "cap_unit": None},
            period={"start": "2026-10-01T00:00:00+09:00", "end": "2026-10-20T23:59:59+09:00"},
            entry={"required": True, "url": "https://paypay.ne.jp/entry/test/"},
            official_url=item["url"], evidence_quote="コンビニで最大10%戻ってくるキャンペーン")]}
    elif item["source_id"] == "dpoint-campaign":
        ans = {"campaigns": []}
    else:
        continue  # 回答しない（次回に再依頼されるはず）
    Path(item["answer_file"]).write_text(json.dumps(ans, ensure_ascii=False), encoding="utf-8")
'''

INGEST = FAKE + r'''
import json, ingest
print(json.dumps(ingest.run(), ensure_ascii=False))
'''

ENV = {**os.environ, "POIKATSU_NOW": "2026-10-05T06:30:00+09:00", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run(repo, code, *args):
    (repo / "_drv.py").write_text(code, encoding="utf-8")
    out = subprocess.run([sys.executable, "_drv.py", *args], cwd=repo, capture_output=True, text=True,
                         encoding="utf-8", env=ENV, timeout=120)
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout.strip().splitlines()[-1]


def read(repo, rel):
    return json.loads((repo / rel).read_text(encoding="utf-8"))


def test_manual_flow(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "work", "__pycache__", ".pytest_cache", "inbox"))
    for p in ["data/campaigns.json", "data/rejected.json"]:
        (repo / p).write_text("[]", encoding="utf-8")
    for p in ["data/holds.json", "data/snapshots.json", "data/source_status.json"]:
        (repo / p).write_text("{}", encoding="utf-8")
    (repo / "reports" / "runs.jsonl").unlink(missing_ok=True)
    shutil.rmtree(repo / "data" / "inbox_done", ignore_errors=True)

    # 1) PC：依頼を作る。詳細ページをたどる3取得元は一覧を依頼せず、見つかった詳細ページだけ依頼する
    prep = json.loads(run(repo, PREPARE))
    assert prep["ok"]
    queue = read(repo, f"inbox/{prep['run_id']}/queue.json")
    ids = [i["source_id"] for i in queue["items"]]
    details = [i for i in queue["items"] if i.get("parent") == "paypay-event"]
    assert len(details) == 1 and details[0]["url"] == "https://paypay.ne.jp/event/test-20261001/"  # きせかえは除外
    assert "paypay-event" not in ids and "vpoint-campaign" not in ids and "waon-campaign" not in ids
    assert len(ids) == 10  # 一覧をそのまま依頼する9取得元 ＋ 詳細ページ1件
    prompt = (repo / details[0]["prompt_file"]).read_text(encoding="utf-8")
    assert "エントリーする：https://paypay.ne.jp/entry/test/" in prompt
    assert read(repo, "data/snapshots.json") == {}  # PC側はスナップショットを触らない

    # 2) PC：ChatGPT の代わりに3件だけ回答を書く → 形チェック
    (repo / "_ans.py").write_text(ANSWER, encoding="utf-8")
    subprocess.run([sys.executable, "_ans.py", f"inbox/{prep['run_id']}/queue.json"], cwd=repo, check=True, env=ENV)
    chk = subprocess.run([sys.executable, "scripts/check_answers.py"], cwd=repo, capture_output=True, text=True,
                         encoding="utf-8", env=ENV)
    res = json.loads(chk.stdout)
    assert chk.returncode == 0, res
    assert len(res["answered"]) == 3

    # 3) GitHub：取り込み
    ing = json.loads(run(repo, INGEST))
    assert ing["ok"] and ing["mode"] == "ingest"
    assert ing["accepted_new"] == 2 and ing["llm_calls"] == 3 and ing["llm_skipped"] == 7
    snaps = read(repo, "data/snapshots.json")
    assert set(snaps) == {"paypay-local", "dpoint-campaign", details[0]["source_id"]}
    assert (repo / "data" / "inbox_done" / prep["run_id"] / "queue.json").exists()
    bundle = read(repo, "dist/staging/poikatsu.json")
    by_title = {c["title"]: c for c in bundle["campaigns"]}
    assert by_title["コンビニで最大10%戻ってくる"]["entry"]["url"] == "https://paypay.ne.jp/entry/test/"
    assert "対象のお店で最大5%戻ってくる" in by_title

    # 4) 次の朝：取り込み済みの詳細ページは再依頼しない。回答しなかった7ページは再依頼される
    prep2 = json.loads(run(repo, PREPARE))
    q2 = read(repo, f"inbox/{prep2['run_id']}/queue.json")
    ids2 = [i["source_id"] for i in q2["items"]]
    assert len(ids2) == 7 and details[0]["source_id"] not in ids2 and "paypay-local" not in ids2
