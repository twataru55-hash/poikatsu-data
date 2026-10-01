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
- 2026-09-26 Codex：オーナー承認により、WordPress確認ページ（ID92）の10個のショートコードを個別ブロックに分割し、部品間の自動改行を解消するための本文修正を実施。
- 2026-09-26 Claude：レビュー対応（詳細ページのたどり・キャンペーン取り違え防止・停止Issueの自動クローズと重複防止・成功日数の暦日集計・使える決済のブランド別確認・Windows の文字コード対応・エントリーURL無しを保留にしない）
- 2026-09-26 Codex：WordPressプラグイン1.0.1の実サイト検証でCocoonとのサイトマップ除外順序の競合を確認し、処理順のみを直した1.0.2を準備。隔離WordPressの7項目に合格（実サイトへの追加適用は確認待ち）。
- 2026-09-26 Codex：オーナー操作でWordPressプラグイン1.0.2へ更新。実サイトで店舗一覧を含む全8 URLの未ログイン404・管理者200/noindex、サイトマップの店舗URL 0件、確認ページID92の表示とパスワード保護維持を確認。
- 2026-09-26 Codex：オーナー承認によりdポイント・d払い・楽天ペイ・楽天ポイントカード・au PAY・イオンの6取得元に詳細ページfollowを追加し、dポイント・d払いの一覧取得をJavaScript描画へ変更。Pontaのfollow追加は保留、許可ドメイン・全体取得上限・公開設定は維持。
- 2026-09-26 Codex：follow追加に伴い、日次通し試験のfollow除去をYAML単位へ修正し、PC通し試験の依頼数を設定に追従させた。空回答の確認対象は一覧取得を維持するPontaへ変更し、採用2件・空回答記録・未回答だけの再依頼を引き続き検査。
- 2026-09-30 Codex：オーナー承認によりエントリー要否・開始/終了日時の不明値を保持して理由付き保留とし、承認・配信経路で不完全な候補を拒否。保留表示と毎朝の回答手順を修正し、回帰テストを追加（公開設定・保留Issueの判断は変更なし）。
- 2026-09-30 Claude：第三者レビュー対応（依頼の公平な順番と後回し分の優先・続けて回答されない依頼の打ち切りと報告・confidence の判断基準を明記・公式エントリーURLからのログイン画面転送を正常扱い・終了日を過ぎた保留Issue・同じ内容が採用済みになった保留Issueの自動クローズ）
- 2026-10-01 Codex：オーナー承認で9/30レビュー修正を取り込み、今回のレポートを選ぶ試験・対象店未登録時の安全な保留・同一IDでも配信内容が異なる保留を閉じない判定を補正。9/28〜9/30の3朝無人実行と9/30オーナー確認に基づきC合格を記録し、毎朝09:00（Asia/Tokyo）で継続（画面ロック試験は省略・未検証、F/Gは未開始）。
- 2026-10-01 Codex：オーナー承認により、9/30レビューの取り直し候補12詳細ページの snapshots 記録だけを保全後に削除し、次回以降の毎朝09:00の抽出対象へ戻した（キャンペーン・保留Issueの判断は変更なし）。
- 2026-10-01: 保留Issueの取得を全ページに対応し、100件を超えると古い保留が処理されない問題を修正。ページ取得4ケースの回帰試験を追加。
