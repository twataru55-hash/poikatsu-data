"""V14：entry.required=false の根拠（記載が無いことを根拠にしない）。"""
import copy

from test_validate import BASE, codes, ctx
from validate import validate_candidate

# 公式ページの構造を模した短い本文（実ページの本文はリポジトリに入れない）
PLUS_PAGE = (
    "いずれかひとつにポイントプラス\n"
    "「dポイントカード」「楽天ポイントカード」「Vポイントアプリ」のいずれか、\n"
    "または各ポイント連携済のファミペイのバーコードをスキャンして対象商品をご購入いただくと、キャンペーンポイントがたまります。\n"
    "※いかなる決済方法でもキャンペーンポイントはたまります。\n"
    "対象店舗でPayPay払いすると最大10%戻ってくる\n期間：2026年10月1日～2026年10月31日\n"
    "ファミペイをダウンロードして\nキャンペーンに参加！\n"
)
PLUS_QUOTE = ("「dポイントカード」「楽天ポイントカード」「Vポイントアプリ」のいずれか、"
              "または各ポイント連携済のファミペイのバーコードをスキャンして対象商品をご購入いただくと、キャンペーンポイントがたまります。")


def cand(quote):
    c = copy.deepcopy(BASE)
    c["entry_evidence_quote"] = quote
    return c


def test_affirmative_procedure_statement_is_accepted():
    # 改行をまたぐ引用・フッターの「キャンペーンに参加！」は問題にしない
    assert validate_candidate(cand(PLUS_QUOTE), ctx(page_text=PLUS_PAGE)) == ("accept", [])


def test_false_without_quote_is_held():
    decision, reasons = validate_candidate(cand(None), ctx(page_text=PLUS_PAGE))
    assert decision == "hold" and any("根拠文（entry_evidence_quote）がない" in r for r in reasons)


def test_quote_not_on_page_is_held():
    decision, reasons = validate_candidate(cand("エントリー不要でポイントがたまります"), ctx(page_text=PLUS_PAGE))
    assert decision == "hold" and any("見つからない" in r and r.startswith("V14") for r in reasons)


def test_quote_without_procedure_shape_is_held():
    page = PLUS_PAGE + "特別価格(税込)：148円\n"
    decision, reasons = validate_candidate(cand("特別価格(税込)：148円"), ctx(page_text=page))
    assert decision == "hold" and any("言い切っていない" in r for r in reasons)


def test_entry_instruction_elsewhere_on_page_is_held():
    page = PLUS_PAGE + "※一部商品は要エントリー。アプリからエントリーしてください。\n"
    decision, reasons = validate_candidate(cand(PLUS_QUOTE), ctx(page_text=page))
    assert decision == "hold" and "V14" in codes(reasons)


def test_explicit_not_required_is_accepted_only_on_clean_page():
    page = PLUS_PAGE + "エントリー不要\n"
    assert validate_candidate(cand("エントリー不要"), ctx(page_text=page)) == ("accept", [])
    mixed = page + "抽選キャンペーンは要エントリー\n"
    decision, reasons = validate_candidate(cand("エントリー不要"), ctx(page_text=mixed))
    assert decision == "hold" and any("明記はあるが" in r for r in reasons)


def test_null_and_true_are_not_affected_by_v14():
    c = cand(None)
    c["entry"]["required"] = None
    decision, reasons = validate_candidate(c, ctx(page_text=PLUS_PAGE))
    assert decision == "hold" and "V14" not in codes(reasons) and any("entry.required" in r for r in reasons)
    c["entry"]["required"] = True
    assert "V14" not in codes(validate_candidate(c, ctx(page_text=PLUS_PAGE))[1])


def test_switch_can_disable_v14():
    cfg = copy.deepcopy(ctx().config)
    cfg["validation"]["require_entry_evidence"] = False
    assert validate_candidate(cand(None), ctx(page_text=PLUS_PAGE, config=cfg)) == ("accept", [])


def test_reask_requests_only_named_detail_page(tmp_path):
    """--reask：取り込み済みで内容が同じ詳細ページを、指定したものだけ再依頼する（--force の全件再依頼を使わない）。"""
    import json
    import subprocess
    import sys
    from test_fairness import _fresh_repo
    from test_manual_e2e import ANSWER, ENV, FAKE, INGEST, PREPARE, read, run

    repo = _fresh_repo(tmp_path)
    prep = json.loads(run(repo, PREPARE))
    queue = read(repo, f"inbox/{prep['run_id']}/queue.json")
    detail = next(i["source_id"] for i in queue["items"] if i.get("parent") == "paypay-event")
    (repo / "_ans.py").write_text(ANSWER, encoding="utf-8")
    subprocess.run([sys.executable, "_ans.py", f"inbox/{prep['run_id']}/queue.json"], cwd=repo, check=True, env=ENV)
    assert json.loads(run(repo, INGEST))["ok"]
    assert detail in read(repo, "data/snapshots.json")

    again = json.loads(run(repo, PREPARE))
    assert detail not in [i["source_id"] for i in read(repo, f"inbox/{again['run_id']}/queue.json")["items"]]
    (repo / "inbox" / again["run_id"] / "queue.json").unlink()

    reask = FAKE + f'''
import json, prepare
print(json.dumps(prepare.main(source_ids=["paypay-event"], reask=["{detail}"]), ensure_ascii=False))
'''
    res = json.loads(run(repo, reask))
    q = read(repo, f"inbox/{res['run_id']}/queue.json")
    assert [i["source_id"] for i in q["items"]] == [detail] and q["reask"] == [detail]
    assert read(repo, "data/snapshots.json")[detail]  # PC側はスナップショットを消さない

    bad = FAKE + '''
import json, prepare
print(json.dumps(prepare.main(reask=["paypay-local"]), ensure_ascii=False))
'''
    (repo / "inbox" / res["run_id"] / "queue.json").unlink()
    (repo / "_drv.py").write_text(bad, encoding="utf-8")
    out = subprocess.run([sys.executable, "_drv.py"], cwd=repo, capture_output=True, text=True, encoding="utf-8", env=ENV)
    assert json.loads(out.stdout.strip().splitlines()[-1])["ok"] is False
