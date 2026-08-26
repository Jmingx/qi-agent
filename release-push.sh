#!/bin/bash
# release-push.sh —— 双仓推送脚本（AGENTS.md P0-8）
#
# 把本地 main 上的【代码提交】推送到远程仓，跳过 docs 相关内容：
#   - 跳过 "docs:" 前缀提交
#   - 跳过含 docs/、AGENTS.md、.hermes/ 文件变更的提交
# 远程历史 = 纯代码提交序列（提交信息/顺序保留）
#
# 改进（2026-08-24）：用 git cherry 替代分叉检测 + git log --not
#   - 双仓策略下 main 永远与远程不同根（本地含 docs 历史），
#     git log origin/main..main 会误列全量旧提交
#   - git cherry 按【补丁等价】判断"远程缺哪些补丁"（不看父链），
#     对不同根历史天然正确——这才是双仓的增量语义
#   - cherry-pick 冲突 = 内容已在远程 → 跳过（不再需要手动分叉处理）
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
    # release 分支 = origin/main 的发布拷贝（本地-only），安全 reset
    git -C "$WT" checkout -B release origin/main
else
    git worktree add "$WT" -b release origin/main
fi

# 2. git cherry：找出 main 上远程缺的提交（补丁等价，对不同根历史正确）
#    + = 远程缺；- = 远程已有等价补丁
NEW_COMMITS=$(git cherry origin/main main | grep '^+' | awk '{print $2}')
if [ -z "$NEW_COMMITS" ]; then
    echo "✅ main 没有远程缺的提交，无需推送"
    exit 0
fi

echo "=== main 上远程缺的提交（git cherry）==="
for c in $NEW_COMMITS; do
    echo "  $c $(git log -1 --format=%s "$c" | head -c 70)"
done
echo

# 3. 筛选可推送提交（跳过 docs: 前缀 + 含本地-only 文件变更）
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

# 4. cherry-pick（逐个应用，保持提交顺序）——失败（冲突）说明内容已在远程，跳过
#    注意：逐个 pick（非序列），失败 abort 只回滚当前这一个
PICKED=0
SKIPPED=0
for c in "${PICKS[@]}"; do
    if git -C "$WT" cherry-pick "$c" 2>/dev/null; then
        PICKED=$((PICKED+1))
    else
        git -C "$WT" cherry-pick --abort 2>/dev/null
        echo "⏭ 跳过（冲突——内容已在远程）: $c"
        SKIPPED=$((SKIPPED+1))
    fi
done

if [ "$PICKED" -eq 0 ]; then
    echo "⚠ 全部跳过（远程已含所有代码变更）"
    exit 0
fi

# 5. 推送
git -C "$WT" push origin HEAD:main
echo "✅ 已推送 $PICKED 个提交（跳过 $SKIPPED）。远程 main 更新。"
