"""週次監査レポート（仕様書 §12）。reports/weekly-YYYYMMDD.md を作る。

オーナーはこのファイルを Claude に渡して監査してもらう。
"""
from __future__ import annotations

import random
from datetime import timedelta

from check_master import verify_online
from common import (
    DATA_DIR,
    DIST_DIR,
    REPORTS_DIR,
    campaign_status,
    is_live,
    load_json,
    now_jst,
    parse_dt,
    read_run_log,
)


def success_days(log: list[dict], now, span: int) -> dict:
    """JST の暦日ごとに「取り込み（または API 方式の日次実行）が1回以上成功したか」を数える。
    対象は今日を含む直近 span 日。導入前の日は分母に入れない。"""
    today = now.date()
    days = [today - timedelta(days=i) for i in range(span)][::-1]
    real = [r for r in log if r.get("mode") in ("ingest", "api")]
    first = min((parse_dt(r["date"]).date() for r in real), default=None)
    ok_days = {parse_dt(r["date"]).date() for r in real if r.get("ok")}
    counted = [d for d in days if first is not None and d >= first]
    missing = [d.isoformat() for d in counted if d not in ok_days]
    return {"span": span, "counted": len(counted), "ok": len(counted) - len(missing), "missing": missing}


def main() -> str:
    now = now_jst()
    campaigns = load_json(DATA_DIR / "campaigns.json", [])
    live = [c for c in campaigns if is_live(campaign_status(c, now))]
    rng = random.Random(now.strftime("%Y%m%d"))
    sample = rng.sample(live, min(10, len(live)))

    runs = [r for r in read_run_log() if parse_dt(r["date"]) >= now - timedelta(days=7)]
    ok_runs = sum(1 for r in runs if r.get("ok"))
    llm_calls = sum(int(r.get("llm_calls", 0)) for r in runs)
    days14 = success_days(read_run_log(), now, 14)
    days7 = success_days(read_run_log(), now, 7)
    holds = load_json(DATA_DIR / "holds.json", {})
    status = load_json(DATA_DIR / "source_status.json", {})
    failing = {k: v for k, v in status.items() if v.get("fail_count", 0) > 0}
    master_failures = verify_online()
    live_meta = (load_json(DIST_DIR / "live" / "poikatsu.json", {}) or {}).get("meta")
    staging_meta = (load_json(DIST_DIR / "staging" / "poikatsu.json", {}) or {}).get("meta")

    L = [
        f"# 週次監査レポート {now.strftime('%Y-%m-%d')}", "",
        "## 1. 実行状況（直近7日）",
        f"- 成功した日：直近7日で {days7['ok']}/{days7['counted']} 日（JST・暦日単位。成功しなかった日：{', '.join(days7['missing']) or 'なし'}）",
        f"- 実行回数（参考）：直近7日で {len(runs)} 回中 {ok_runs} 回成功",
        f"- モデル呼び出し：合計 {llm_calls} 回",
        f"- 配信 staging：{(staging_meta or {}).get('data_version')} ／ live：{(live_meta or {}).get('data_version')}",
        f"- 実施中キャンペーン：{len(live)} 件",
        "",
        "## 2. 抜き取り10件（公式ページと照らして、還元率・期間・対象店・エントリー要否が正しいか確認）",
        "| No | タイトル | 還元 | 期間 | 対象 | エントリー | 公式URL | 根拠文 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(sample, 1):
        s = c.get("scope", {})
        target = ", ".join(s.get("store_ids", []) + s.get("prefecture_codes", [])) or s.get("kind")
        L.append(
            f"| {i} | {c['title']} | {c['benefit']['rate_text']} | {c['period']['start'][:10]}〜{c['period']['end'][:10]} "
            f"| {target} | {'要' if c['entry']['required'] else '不要'} | {c['official_url']} | {c['evidence_quote'].replace('|', '／')[:120]} |"
        )
    L += ["", "## 3. 保留中", f"- {len(holds)} 件（GitHub の Issue で hold ラベルを確認）"]
    for cid, h in list(holds.items())[:30]:
        L.append(f"  - #{h.get('issue')} {cid}（{h.get('created')}）：{' / '.join(h.get('reasons', []))[:120]}")
    L += ["", "## 4. 取得に失敗している取得元"]
    L += [f"- {k}：{v.get('fail_count')} 回連続／{v.get('last_error')}" for k, v in failing.items()] or ["- なし"]
    L += ["", "## 5. マスタ（常設の還元・定例日）の根拠確認"]
    L += [f"- {m}" for m in master_failures] or ["- すべて確認できた"]
    L += ["", "## 6. 本番切替の条件（§12）",
          f"- 日次実行 13/14 日以上成功：直近14日で {days14['ok']}/{days14['counted']} 日"
          + ("（14日分そろっていないため、まだ判定できない）" if days14['counted'] < 14 else ("（満たす）" if days14['ok'] >= 13 else "（満たさない）")),
          "- 週次監査2回で重大な誤り0件：Claude の監査結果で判断",
          "- 未解決の [異常] Issue 0件：GitHub の anomaly ラベルで確認"]
    text = "\n".join(L) + "\n"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"weekly-{now.strftime('%Y%m%d')}.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    from common import setup_utf8_stdout

    setup_utf8_stdout()
    print(main())
