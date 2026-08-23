#!/bin/bash
# release-push.sh —— 双仓推送脚本（AGENTS.md P0-8）
#
# 把本地 main 上的【代码提交】推送到远程仓，跳过 docs 相关内容：
#   - 跳过 "docs:" 前缀提交
#   - 跳过含 docs/、AGENTS.md、.hermes/ 文件变更的提交
# 远程历史 = 纯代码提交序列（提交信息/顺序保留）
#
# 用法：bash release-push.sh   （推送前打印提交清单供确认）
set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
# git 是原生 Windows 程序，不接受 MSYS 路径（/c/...）——统一转 C:/ 风格
REPO_WIN="$(cygpath -m "$REPO")"
WT="$REPO_WIN/.release-wt"   # worktree（gitignore，本地-only）

cd "$REPO"
git fetch origin

# 1. 建立/同步发布 worktree（基线 = origin/main）
if [ -d "$WT" ]; then
    git -C "$WT" fetch origin
else
    git worktree add "$WT" -b release origin/main
fi

# 2. 找出 main 上新增的提交（相对 origin/main——release 基线）
# 注意：--not 排除的是分支引用（worktree 路径不行）
NEW_COMMITS=$(git log --oneline main --not origin/main | awk '{print $1}')
if [ -z "$NEW_COMMITS" ]; then
    echo "✅ main 没有新提交，无需推送"
    exit 0
fi

echo "=== main 领先发布分支的提交 ==="
git log --oneline main --not origin/main
echo

# 3. 筛选可推送提交（跳过 docs: 前缀 + 含 docs 文件变更）
PICKS=()
for c in $NEW_COMMITS; do
    MSG=$(git log -1 --format=%s "$c")
    if [[ "$MSG" == docs:* ]]; then
        echo "⏭ 跳过（docs 提交）: $c $MSG"
        continue
    fi
    if git show --name-only --format= "$c" | grep -qE "^(docs/|AGENTS\.md$|\.hermes/|release-push\.sh$)"; then
        echo "⏭ 跳过（含本地-only 文件变更）: $c $MSG"
        continue
    fi
    PICKS+=("$c")
done

if [ ${#PICKS[@]} -eq 0 ]; then
    echo "⚠ 没有可推送的代码提交（全部是 docs 提交）"
    exit 0
fi

echo
echo "=== 将推送到远程 main 的提交 ==="
for c in "${PICKS[@]}"; do
    echo "  $c $(git log -1 --format=%s "$c")"
done
echo
read -p "确认推送？（y/N）" -r
if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 1
fi

# 4. cherry-pick（保持提交顺序）——按 main 上的原始顺序逐个应用
for c in $(echo "${PICKS[@]}" | tr ' ' '\n' | tac); do
    git -C "$WT" cherry-pick "$c"
done

# 5. 推送
git -C "$WT" push origin HEAD:main
echo "✅ 已推送。远程 main 更新。"
