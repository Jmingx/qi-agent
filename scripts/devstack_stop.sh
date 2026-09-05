#!/bin/bash
# devstack_stop.sh —— qi-agent 开发环境停止（进程；--all 连容器一起停）
#
# 用法：
#   ./devstack_stop.sh         停进程：web、serve（及旧版 jaeger exe 遗留进程）
#   ./devstack_stop.sh --all   额外停容器：Opik 容器组 + Jaeger 容器删除
#
# 设计（2026-09-06，配合 devstack_start.sh）：
#   - 跨 shell 可用（git-bash / WSL 都行）：进程停止按 netstat 找 Windows 监听 pid，
#     再用 taskkill.exe 杀——不依赖 pid 文件的 shell 语义（MSYS pid vs Linux pid 有别）；
#     pid 文件仅作遗留清理线索。
#   - 进程（serve/web）轻量随时停；容器启动成本高且数据在 named volume →
#     默认保留，--all 才连锅端；--all 后重跑 start 可秒回（镜像/volume 仍在）。
#   - Opik 容器停止：优先 compose down（目录在，优雅停+清 network）；
#     目录不在（Temp 缓存被清）→ 按容器名前缀 opik- 逐个 rm -f 兜底。
#   - 兼容迁移：旧版 devstack 用 jaeger 独立 exe（pid 文件 jaeger.pid），一并清理。

set -u
set -o pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# ---------- 跨 shell 路径解析（git-bash / WSL 通用，同 devstack_start.sh） ----------
is_wsl() { [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; }

if [ -n "${USERPROFILE:-}" ]; then
  WIN_HOME="$USERPROFILE"
elif command -v cmd.exe >/dev/null 2>&1; then
  WIN_HOME="$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r\n')"   # C:\Users\xie
elif is_wsl; then
  WIN_HOME="/mnt/c/Users/$(id -un)"                                          # /mnt/c/Users/xie
else
  WIN_HOME="$HOME"
fi
WIN_HOME="$(printf '%s' "$WIN_HOME" | sed 's|\\|/|g')"   # C:\Users\xie → C:/Users/xie
# 注：不用 ${var//\\\\//}——bash 4.4（git-bash）与 5.2（WSL）对反斜杠转义解析不同，9/6 实测失效
if is_wsl && [ "${WIN_HOME#[A-Za-z]:}" != "$WIN_HOME" ]; then                # WSL: C:/x → /mnt/c/x
  WIN_HOME="/mnt/$(printf '%s' "${WIN_HOME%:*}" | tr 'A-Z' 'a-z')${WIN_HOME#*:}"
fi

DEVSTACK_DIR="$WIN_HOME/AppData/Local/Temp/devstack"   # pid 目录（与 start 同一位置）

OPIK_SELFHOST_DIR="$WIN_HOME/AppData/Local/Temp/opik-selfhost"
OPIK_COMPOSE="$OPIK_SELFHOST_DIR/deployment/docker-compose"
OPIK_PROJECT="opik"
OPIK_COMPOSE_FILES=(-f docker-compose.yaml -f docker-compose.override.yaml)
JAEGER_CONTAINER="qi-jaeger"
SERVE_PORT=8771
WEB_PORT=9004

# ---------- 工具函数 ----------
say()  { printf '\033[1;34m[devstack]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; }
pid_file() { echo "$DEVSTACK_DIR/$1.pid"; }

stop_port_proc() {  # stop_port_proc <端口> <名字> —— netstat 找监听进程（Windows pid）→ taskkill 杀
  local port="$1" name="$2" pid
  # netstat.exe 输出 CRLF——pid 必须去 \r，否则 taskkill 报错
  pid="$(netstat.exe -ano 2>/dev/null | grep ":$port .*LISTENING" | awk '{print $NF}' | tr -d '\r' | head -1)"
  if [ -z "$pid" ]; then
    say "$name 未在运行（端口 $port 无监听）"
    return 0
  fi
  # MSYS_NO_PATHCONV=1：git-bash 下禁路径转换（/PID 不被改成盘符路径）；WSL 无此变量无影响
  if MSYS_NO_PATHCONV=1 taskkill.exe /PID "$pid" /F >/dev/null 2>&1; then
    ok "$name 已停止（pid $pid）"
  elif kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null; then
    ok "$name 已停止（kill $pid）"
  else
    fail "$name 停止失败（pid $pid）——请手动 taskkill /PID $pid /F"
  fi
}

stop_pid() {  # stop_pid <pid文件> <名字> —— 按 pid 文件清理（git-bash 语义，兼容旧版遗留）
  local pf="$1" name="$2" pid
  if [ -f "$pf" ]; then
    pid="$(cat "$pf")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null; sleep 1
      kill -9 "$pid" 2>/dev/null
      ok "$name 已停止（pid 文件 $pid）"
    else
      say "$name pid 文件残留（进程 $pid 已不在）——清理"
    fi
    rm -f "$pf"
  fi
}

stop_processes() {  # 停进程组：web → serve → 旧版 jaeger exe 遗留
  stop_port_proc "$WEB_PORT"   "web（进程）"
  stop_port_proc "$SERVE_PORT" "serve（进程）"
  # 新版 jaeger 是容器（无 pid 文件）；若 jaeger.pid 存在 = 旧版 exe 遗留（git-bash 起的）
  stop_pid "$(pid_file jaeger)" "jaeger（旧版 exe 遗留）"
}

stop_containers() {  # 容器组：Opik 容器组 + Jaeger 容器删除（--all 专用，跨环境不依赖 compose 插件）
  if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon 不可用（docker info 失败）——容器跳过"
    return 1
  fi
  # Opik：compose 目录在 → 优雅 compose down（数据保留 volume）；目录不在 → 按名前缀兜底删除
  if [ -d "$OPIK_COMPOSE" ]; then
    if (cd "$OPIK_COMPOSE" && docker compose -p "$OPIK_PROJECT" \
        "${OPIK_COMPOSE_FILES[@]}" --profile opik down) >/dev/null 2>&1; then
      ok "Opik 容器已停止（compose down——数据保留在 named volume）"
    else
      fail "Opik compose down 失败——尝试按容器名前缀兜底删除…"
      local ids
      ids="$(docker ps -aq --filter "name=^opik-" 2>/dev/null)"
      [ -n "$ids" ] && docker rm -f $ids >/dev/null 2>&1 \
        && ok "Opik 容器已兜底删除（$(echo "$ids" | wc -l | tr -d ' ') 个，volume 保留）" \
        || fail "Opik 容器兜底删除也失败——请手动 docker ps -a 检查"
    fi
  else
    say "Opik compose 目录不存在（$OPIK_COMPOSE）——按容器名前缀 opik- 兜底删除…"
    local ids
    ids="$(docker ps -aq --filter "name=^opik-" 2>/dev/null)"
    if [ -n "$ids" ]; then
      docker rm -f $ids >/dev/null 2>&1 \
        && ok "Opik 容器已删除（$(echo "$ids" | wc -l | tr -d ' ') 个，volume 保留）"
    else
      say "Opik 容器不存在——跳过"
    fi
  fi
  # Jaeger 单容器
  if docker ps -a --format '{{.Names}}' | grep -qx "$JAEGER_CONTAINER"; then
    docker rm -f "$JAEGER_CONTAINER" >/dev/null 2>&1 \
      && ok "Jaeger 容器已删除（$JAEGER_CONTAINER）"
  else
    say "Jaeger 容器不存在——跳过"
  fi
}

# ---------- 主流程 ----------
case "${1:-}" in
  --all)
    say "停止全部（进程 + 容器）…"
    stop_processes
    echo
    say "停止容器…"
    stop_containers
    echo
    say "全部停止完成——重跑 scripts/devstack_start.sh（git-bash）可随时恢复（镜像/volume 仍在）"
    ;;
  *)
    say "停止进程（容器保留——如连容器一起停：$0 --all）…"
    stop_processes
    echo
    say "进程已停止。Opik/Jaeger 容器未动，重跑 devstack_start.sh 会直接复用。"
    ;;
esac
