#!/usr/bin/env bash
# 変更をコミットして push する（他の実行と重なったら取り込み直して再試行）
set -eu
msg="${1:-update}"
git config user.name "poikatsu-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
# 取り込み後の inbox は空になり、次の checkout には存在しないことがある。
# 存在するか追跡済みの許可対象だけを追加し、保存エラーは成功扱いにしない。
for path in data dist reports master inbox; do
  tracked=$(git ls-files -- "$path")
  if [[ -e "$path" || -n "$tracked" ]]; then
    git add -A -- "$path"
  fi
done
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
