"""PC抽出方式（manual）の通し試験：prepare → 回答を書く → check_answers → ingest。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FAKE = r'''
import sys
sys.path.insert(0, "scripts")
import collect, validate
PAGE = "対象のお店でPayPay払いすると最大5%戻ってくる\n期間：2026年10月1日～2026年10月31日"
class FakeFetcher(collect.Fetcher):
    def allowed(self, url): return True
    def get_static(self, url): return "<html><body><main>" + PAGE.replace("\n", "<br>") + "</main></body></html>"
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
q = json.loads(Path(sys.argv[1]).read_text())
for item in q["items"]:
    if item["source_id"] == "paypay-event":
        ans = {"campaigns": [{
            "type": "reward", "brand_ids": ["paypay"], "title": "対象のお店で最大5%戻ってくる",
            "benefit": {"rate_max": 5, "rate_text": "最大5%還元", "cap_per_use": None, "cap_total": None, "cap_unit": None},
            "scope": {"kind": "payment_wide", "store_ids": [], "prefecture_codes": [], "municipality": None},
            "period": {"start": "2026-10-01T00:00:00+09:00", "end": "2026-10-31T23:59:59+09:00"},
            "entry": {"required": False, "url": None}, "conditions": None,
            "official_url": "https://paypay.ne.jp/event/", "evidence_quote": "対象のお店でPayPay払いすると最大5%戻ってくる",
            "confidence": "confirmed"}]}
    elif item["source_id"] == "vpoint-campaign":
        ans = {"campaigns": []}
    else:
        continue  # 回答しない（次回に再依頼されるはず）
    Path(item["answer_file"]).write_text(json.dumps(ans, ensure_ascii=False))
'''

INGEST = FAKE + r'''
import json, ingest, pipeline
print(json.dumps(ingest.run(), ensure_ascii=False))
'''


def run(repo, code, *args):
    env = {"POIKATSU_NOW": "2026-10-05T06:30:00+09:00", "PATH": "/usr/bin:/bin"}
    (repo / "_drv.py").write_text(code, encoding="utf-8")
    out = subprocess.run([sys.executable, "_drv.py", *args], cwd=repo, capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout.strip().splitlines()[-1]


def test_manual_flow(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "work", "__pycache__", ".pytest_cache", "inbox"))
    for p in ["data/campaigns.json", "data/rejected.json"]:
        (repo / p).write_text("[]")
    for p in ["data/holds.json", "data/snapshots.json", "data/source_status.json"]:
        (repo / p).write_text("{}")
    (repo / "reports" / "runs.jsonl").unlink(missing_ok=True)

    # 1) PC：依頼を作る（全ページが初回なので「変わった」扱い）
    prep = json.loads(run(repo, PREPARE))
    assert prep["ok"] and len(prep["to_answer"]) == 12
    queue_path = repo / "inbox" / prep["run_id"] / "queue.json"
    assert queue_path.exists()
    assert (repo / "work" / "prompts" / "paypay-event.md").read_text().count("inbox/") >= 1
    assert json.loads((repo / "data/snapshots.json").read_text()) == {}  # PC側はスナップショットを触らない

    # 2) PC：ChatGPT の代わりに2件だけ回答を書く → 形チェック
    (repo / "_ans.py").write_text(ANSWER, encoding="utf-8")
    subprocess.run([sys.executable, "_ans.py", str(queue_path)], cwd=repo, check=True)
    chk = subprocess.run([sys.executable, "scripts/check_answers.py"], cwd=repo, capture_output=True, text=True,
                         env={"PYTHONPATH": "scripts", "PATH": "/usr/bin:/bin"})
    res = json.loads(chk.stdout)
    assert chk.returncode == 0 and sorted(res["answered"]) == ["paypay-event", "vpoint-campaign"]

    # 3) GitHub：取り込み
    ing = json.loads(run(repo, INGEST))
    assert ing["ok"] and ing["mode"] == "ingest"
    assert ing["accepted_new"] == 1 and ing["llm_calls"] == 2 and ing["llm_skipped"] == 10
    snaps = json.loads((repo / "data/snapshots.json").read_text())
    assert set(snaps) == {"paypay-event", "vpoint-campaign"}  # 回答が来たページだけ更新
    assert not (repo / "inbox" / prep["run_id"]).exists()
    assert (repo / "data" / "inbox_done" / prep["run_id"] / "queue.json").exists()
    bundle = json.loads((repo / "dist/staging/poikatsu.json").read_text())
    assert [c["title"] for c in bundle["campaigns"]] == ["対象のお店で最大5%戻ってくる"]

    # 4) 次の朝：回答しなかった10ページだけが再依頼される
    prep2 = json.loads(run(repo, PREPARE))
    assert len(prep2["to_answer"]) == 10
    assert "work/prompts/paypay-event.md" not in prep2["to_answer"]
