#!/usr/bin/env bash
# 変更をコミットして push する（他の実行と重なったら取り込み直して再試行）
set -u
msg="${1:-update}"
git config user.name "poikatsu-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A data dist reports master inbox 2>/dev/null || true
if git diff --cached --quiet; then
  echo "変更なし"
  exit 0
fi
git commit -m "$msg" >/dev/null
for i in 1 2 3; do
  if git push; then exit 0; fi
  git pull --rebase || { git rebase --abort; git pull --no-rebase -X ours; }
  sleep $((i * 5))
done
echo "push に失敗しました" >&2
exit 1
