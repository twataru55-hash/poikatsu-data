# poikatsu-data

ポイ活攻略（https://gtdwmse.com/poikatsu/）のキャンペーンデータを毎日作るリポジトリです。
仕様の正本は「ポイ活攻略_実装仕様書」。ここにはデータと仕組みだけを置きます。

## 毎日の流れ（現在：PC抽出方式 `llm.provider: manual`）
1. 毎朝、オーナーのPCで ChatGPT デスクトップアプリのスケジュール済みタスクが動く（手順は `AGENTS.md`）
   - `scripts/prepare.py` が公式ページを取得し、前日から変わったページの依頼を作る
   - ChatGPT が依頼を読んで項目を抜き出し、`inbox/` に回答を書く → `scripts/check_answers.py` で形を確認 → push
2. push されると GitHub の `ingest` が動き、公式ページを取り直して機械チェック（V01〜V13）
   - 合格したものだけ自動で採用。それ以外は Issue（`hold` ラベル）で保留
3. `dist/staging/poikatsu.json`（確認用）を作る。本番切替後は `dist/live/poikatsu.json` も作る
4. WordPress のプラグインが1時間ごとに取りに来て表示する
5. `watchdog` が毎日12時に、36時間以上更新が止まっていないか確かめる（止まっていれば Issue）

API方式に戻すときは、`config/pipeline.yaml` の `llm.provider` を `anthropic` か `openai` にして、GitHub Secrets に API キーを登録する（`daily-api` ワークフローが毎朝動く）。

## オーナーがやること
- **保留の判断**：Issue を開き、公開してよければ `approve`、公開しないなら `reject` ラベルを付ける
- **週1回**：金曜にできる `reports/weekly-日付.md` を Claude に見せて監査してもらう
- **止めたいとき**：`config/pipeline.yaml` の `enabled` を `false` にする

## フォルダ
| 場所 | 中身 | 誰が触るか |
|---|---|---|
| config/ | 実行設定・巡回先リスト | Claude（オーナー承認） |
| master/ | ブランド・店・定例日・みのりの一言・PRリンク | Claude（オーナー承認）。pr_links.json はオーナー |
| data/ | 採用済みキャンペーンと実行記録 | 自動 |
| dist/ | WordPress に渡す配信ファイル | 自動 |
| reports/ | 実行結果・週次監査 | 自動 |
| scripts/ | 処理本体 | Claude |
| inbox/ | PCから届いた回答（取り込み後は data/inbox_done/ へ移動） | PC の ChatGPT |
| AGENTS.md | 毎朝の作業手順（ChatGPT が読む） | Claude |

## 必要な設定
- PC抽出方式（現在）：GitHub の Secrets は不要。PC に Python・git・このリポジトリのクローンが必要
- API方式にするときだけ：GitHub の Settings → Secrets and variables → Actions に `ANTHROPIC_API_KEY` または `OPENAI_API_KEY`

## 手元での確認（開発者向け）
```
pip install -r requirements.txt
python -m pytest -q
PYTHONPATH=scripts python scripts/check_master.py
```

## 変更履歴
- 2026-09-26 Claude：初版
- 2026-09-26 Claude：PC抽出方式（ChatGPT デスクトップのスケジュール）に対応。prepare / check_answers / ingest / watchdog を追加
- 2026-09-26 Codex：PC抽出の無人実行に向け、PC側にポイ活専用のコマンド許可ルールを追加（抽出・配信の実装と設定は変更なし）。
- 2026-09-26 Codex：オーナー承認により、スマホ表示確認のためWordPressの確認ページ（ID92）のCocoonページタイプを「本文のみ（広い）」、タイトル非表示に変更。
