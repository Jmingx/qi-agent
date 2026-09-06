#!/bin/bash
# devstack_start.sh —— qi-agent 开发环境一键启动（可观测 + 评测 + 内核）
#
# 启动组件：
#   1. Opik    评测平台   docker compose（容器，镜像预检）      UI http://127.0.0.1:5173
#   2. Jaeger  调用链     docker 单容器（镜像预检）              UI http://127.0.0.1:16686/jaeger
#   3. serve   内核 WS 服务（进程，OTel 导出 → Jaeger :4318）   ws://127.0.0.1:8771
#   4. web     Web Bridge + 前端（进程，含 /jaeger 同源反代）   http://127.0.0.1:9004
#
# 用法：
#   ./devstack_start.sh            一键启动（幂等——已运行的服务跳过）
#   ./devstack_start.sh --status   查看各服务状态
#   配套停止：scripts/devstack_stop.sh（停进程）/ 加 --all 连容器一起停
#
# 设计（2026-09-06 重构，替代原 devstack.sh）：
#   - 容器与进程分治：Opik/Jaeger 走 docker（好管理）；serve/web 仍为 uv run 进程
#   - 镜像策略：有本地镜像 → 直接启动（compose 加 --pull never，不访问 registry）；
#     缺镜像 → 明确提示 + 给出准备命令，绝不自动 pull（Opik 镜像集大，由用户手动准备后重跑）
#   - compose 单入口：固定 -p opik + 项目文件 + profile（复用现有容器/volume，不另起一套）
#   - 幂等：HTTP 探测 / 容器状态 / pid 文件 + 端口预检，重复执行不双起
#   - 健壮：每个组件失败不中断后续，主流程汇总报告；日志写 $DEVSTACK_DIR/logs/
#   - 依赖：Docker Desktop（Opik/Jaeger 需要）、本地镜像

set -u
set -o pipefail   # 管道退出码取第一个失败命令（compose|tee 不吞错误）

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# ---------- 跨 shell 路径解析（git-bash / WSL 通用） ----------
# 目标：解析出 Windows 用户目录——git-bash（MINGW）下为 C:/Users/xie（bash 与
# native 工具通吃）；WSL（Linux）下为 /mnt/c/Users/xie（WSL bash 只认 Linux 路径）。
# 取值顺序：USERPROFILE（git-bash 原生有）→ cmd.exe 桥接（WSL interop）→
#           /mnt/c/Users/<user> 拼接（WSL 无 interop）→ $HOME 兜底。
# 背景：git-bash 的 $HOME 可能是 MSYS 路径（/home/xie）；WSL 里 USERPROFILE 默认
# 不在环境里——直接用 $HOME 拼 Windows 路径在两种 shell 下都会错（9/3、9/6 两度踩坑）。
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

# pid/日志目录统一放在 Windows Temp（git-bash 与 WSL 视角是同一物理位置）
DEVSTACK_DIR="$WIN_HOME/AppData/Local/Temp/devstack"
LOGS_DIR="$DEVSTACK_DIR/logs"
mkdir -p "$LOGS_DIR"

OPIK_REPO_URL="https://github.com/comet-ml/opik.git"
OPIK_SELFHOST_DIR="$WIN_HOME/AppData/Local/Temp/opik-selfhost"
OPIK_COMPOSE="$OPIK_SELFHOST_DIR/deployment/docker-compose"
OPIK_PROJECT="opik"                            # compose 项目名（容器名 opik-* 前缀，必须固定复用）
OPIK_COMPOSE_FILES=(-f docker-compose.yaml -f docker-compose.override.yaml)
WEB_TOKEN_FILE="$WIN_HOME/.qi-agent/web_token"

JAEGER_IMAGE="jaegertracing/all-in-one:1.66.0" # 与旧 exe 版同版本；容器化部署
JAEGER_CONTAINER="qi-jaeger"

SERVE_PORT=8771
WEB_PORT=9004
OPIK_UI=http://127.0.0.1:5173/
JAEGER_UI=http://127.0.0.1:16686/jaeger/

# ---------- 工具函数 ----------
say()  { printf '\033[1;34m[devstack]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; }
step() { printf '\033[1;36m[devstack]\033[0m ── %s ──\n' "$*"; }
pid_file() { echo "$DEVSTACK_DIR/$1.pid"; }

is_up() {  # is_up <url> —— HTTP 200 即视为就绪
  # 用命令替换捕获 http_code 再比较，不用 `curl | grep` 管道：
  # git-bash 里 Windows 原生 curl.exe 写 stdout 到 MSYS 管道/pty 可能报退出码 23
  # （write error），set -o pipefail 下会整体判失败——即使 grep 已匹配 200。
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null)"
  [ "$code" = "200" ]
}

wait_up() {  # wait_up <url> <名字> <超时秒> —— 每 2s 探测，每 30s 打一行进度（长等待不静默）
  local url="$1" name="$2" timeout="${3:-60}" i waited=0
  for ((i = 0; i < timeout; i += 2)); do
    is_up "$url" && { ok "$name 就绪 ($url)"; return 0; }
    waited=$((waited + 2))
    if (( waited % 30 == 0 )); then
      say "  $name 启动中…已等待 ${waited}s（上限 ${timeout}s）"
    fi
    sleep 2
  done
  fail "$name 未就绪（${timeout}s 超时）→ $url"
  return 1
}

proc_alive() {  # proc_alive <pid文件> —— pid 文件存在且进程活着
  local pf="$1"
  [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null
}

start_bg() {  # start_bg <名字> <pid文件> <命令...>  —— 后台起 + 记 pid + 日志
  local name="$1" pf="$2"; shift 2
  if proc_alive "$pf"; then
    say "$name 已在运行（pid $(cat "$pf")）——跳过"
    return 0
  fi
  nohup "$@" > "$LOGS_DIR/$name.log" 2>&1 &
  echo $! > "$pf"
  say "$name 启动中（pid $!，日志 $LOGS_DIR/$name.log）"
}

start_docker() {  # Docker daemon 没起则自动拉起 Docker Desktop 并等待就绪（最多 4.5 分钟）
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon 已运行"
    return 0
  fi
  say "Docker daemon 未运行——尝试启动 Docker Desktop…"
  if [ -f "/c/Program Files/Docker/Docker/Docker Desktop.exe" ]; then
    "/c/Program Files/Docker/Docker/Docker Desktop.exe" &
  else
    fail "未找到 Docker Desktop（需要手动启动）"
    return 1
  fi
  for ((i = 0; i < 90; i += 3)); do
    docker info >/dev/null 2>&1 && { ok "Docker daemon 就绪"; return 0; }
    sleep 3
  done
  fail "Docker daemon 启动超时"
  return 1
}

# ---------- Opik（docker compose） ----------
opik_compose() {  # opik_compose <arg...> —— compose 调用单入口（项目名/文件/profile 固定在此）
  (cd "$OPIK_COMPOSE" && docker compose -p "$OPIK_PROJECT" \
      "${OPIK_COMPOSE_FILES[@]}" --profile opik "$@")
}

opik_missing_images() {  # 输出缺失镜像名（多行；空 = 齐全）。以 compose 声明清单为准
  opik_compose config --images 2>/dev/null | sort -u | while read -r img; do
    [ -z "$img" ] && continue
    docker image inspect "$img" >/dev/null 2>&1 || echo "$img"
  done
}

start_opik() {
  if is_up "$OPIK_UI"; then
    ok "Opik 已在运行 ($OPIK_UI)"
    return 0
  fi
  step "Opik 评测平台（docker compose：镜像预检，缺则提示）"
  start_docker || { fail "Docker daemon 不可用——无法启动 Opik"; return 1; }
  if [ ! -d "$OPIK_COMPOSE" ]; then
    fail "未找到 Opik compose 文件：$OPIK_COMPOSE"
    say "  准备（Temp 缓存被清后需重建）：git clone --depth 1 $OPIK_REPO_URL \"$OPIK_SELFHOST_DIR\""
    return 1
  fi
  local missing
  missing="$(opik_missing_images)"
  if [ -n "$missing" ]; then
    fail "本地缺 Opik 所需镜像 $(echo "$missing" | wc -l | tr -d ' ') 个（devstack 不自动 pull）："
    echo "$missing" | sed 's/^/    - /'
    say "  准备：cd \"$OPIK_COMPOSE\" && docker compose pull（registry mirror 已配置），之后重跑本脚本"
    return 1
  fi
  say "本地镜像齐全——compose 拉起容器（--pull never 强制本地，不访问 registry）…"
  if ! opik_compose up -d --pull never 2>&1 | tee -a "$LOGS_DIR/opik.log"; then
    fail "Opik 容器启动失败——上方是最后输出，完整日志 $LOGS_DIR/opik.log"
    return 1
  fi
  wait_up "$OPIK_UI" "Opik" 240
}

# ---------- Jaeger（docker 单容器） ----------
start_jaeger() {
  step "Jaeger 调用链（docker 容器：镜像预检，缺则提示）"
  start_docker || { fail "Docker daemon 不可用——无法启动 Jaeger"; return 1; }
  # 容器已存在：running → 跳过；stopped → start
  if docker ps -a --format '{{.Names}}' | grep -qx "$JAEGER_CONTAINER"; then
    if [ "$(docker inspect -f '{{.State.Running}}' "$JAEGER_CONTAINER")" = "true" ]; then
      ok "Jaeger 容器已在运行（$JAEGER_CONTAINER，$JAEGER_UI）"
      return 0
    fi
    docker start "$JAEGER_CONTAINER" >/dev/null && say "Jaeger 容器已启动（stopped → start）"
    wait_up "$JAEGER_UI" "Jaeger" 60
    return $?
  fi
  # 容器不存在：16686 被非容器进程占用（旧版 jaeger exe 遗留）→ 挡下提示
  if netstat.exe -ano 2>/dev/null | grep -q ":16686 .*LISTENING"; then
    fail "16686 已被其它进程占用（旧版 jaeger exe？）——先执行 scripts/devstack_stop.sh 清掉遗留进程"
    return 1
  fi
  if ! docker image inspect "$JAEGER_IMAGE" >/dev/null 2>&1; then
    fail "本地无 jaeger 镜像 $JAEGER_IMAGE（devstack 不自动 pull）"
    say "  准备：docker pull $JAEGER_IMAGE，之后重跑本脚本即可"
    return 1
  fi
  say "本地镜像齐全——创建容器 $JAEGER_CONTAINER…"
  # 只映射用到的端口：16686（UI）+ 4318（serve 的 OTLP HTTP 上报）；参数同旧 exe 版
  # MSYS_NO_PATHCONV=1：git-bash 会把 /jaeger 这类参数转成 Windows 路径
  # （C:/Program Files/Git/jaeger）→ jaeger panic。docker run 无路径参数需转换，整条禁用安全。
  MSYS_NO_PATHCONV=1 docker run -d --name "$JAEGER_CONTAINER" -p 16686:16686 -p 4318:4318 \
    "$JAEGER_IMAGE" --query.base-path=/jaeger --collector.otlp.enabled=true \
    || { fail "docker run 失败（$JAEGER_CONTAINER）"; return 1; }
  wait_up "$JAEGER_UI" "Jaeger" 60
}

# ---------- serve / web（uv run 进程） ----------
start_serve() {
  local pf
  pf="$(pid_file serve)"
  step "内核 serve（进程）"
  if netstat.exe -ano 2>/dev/null | grep -q ":$SERVE_PORT .*LISTENING"; then
    ok "serve 已在运行 (ws://127.0.0.1:$SERVE_PORT)"
    return 0
  fi
  # 端口没监听但 pid 文件进程还活着（半死：进程在、服务没起来）→ 先清掉再起，避免双进程
  if proc_alive "$pf"; then
    say "serve 进程（pid $(cat "$pf")）在但端口未监听——清理后重启"
    kill "$(cat "$pf")" 2>/dev/null; sleep 1
    kill -9 "$(cat "$pf")" 2>/dev/null; rm -f "$pf"
  fi
  # PYTHONUTF8=1：Windows 中文系统 stdout 默认 GBK，代码里 print("✓") 等非 GBK 字符
  # 直接 UnicodeEncodeError 崩（用户裸 shell 实测）——强制 UTF-8 模式
  start_bg serve "$pf" env OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 PYTHONPATH= PYTHONUTF8=1 uv run python -m qi_agent.serve --port "$SERVE_PORT"
  # serve 就绪检查（WS 端口——用 TCP 探测）
  for ((i = 0; i < 30; i += 2)); do
    netstat.exe -ano 2>/dev/null | grep -q ":$SERVE_PORT .*LISTENING" && { ok "serve 就绪 (ws://127.0.0.1:$SERVE_PORT)"; return 0; }
    sleep 2
  done
  fail "serve 未就绪——日志 $LOGS_DIR/serve.log"
  return 1
}

start_web() {
  local pf
  pf="$(pid_file web)"
  step "Web 前端（进程）"
  if netstat.exe -ano 2>/dev/null | grep -q ":$WEB_PORT .*LISTENING"; then
    ok "web 已在运行 (http://127.0.0.1:$WEB_PORT)"
    return 0
  fi
  # 同上：半死进程先清掉再起
  if proc_alive "$pf"; then
    say "web 进程（pid $(cat "$pf")）在但端口未监听——清理后重启"
    kill "$(cat "$pf")" 2>/dev/null; sleep 1
    kill -9 "$(cat "$pf")" 2>/dev/null; rm -f "$pf"
  fi
  # 注意：web 必须在 serve 之后起（Bridge 不会自动重连 serve——2026-09-03 踩坑）
  start_bg web "$pf" env PYTHONPATH= PYTHONUTF8=1 uv run python -m qi_agent.web.server --port "$WEB_PORT" --serve "ws://127.0.0.1:$SERVE_PORT"
  wait_up "http://127.0.0.1:$WEB_PORT/" "web" 60
}

# ---------- 主流程 ----------
# WSL 检测：serve/web 依赖 Windows 版 uv/python 环境，完整启动须在 git-bash 运行；
# 停止服务不受限（devstack_stop.sh 已跨环境）。
if is_wsl; then
  echo "[devstack] ✗ 检测到 WSL 环境——serve/web 进程依赖 Windows 版 uv/python，"
  echo "          完整启动请在 Windows git-bash（MSYS）里运行本脚本。"
  echo "          停止服务（进程+容器）在 WSL 可用：scripts/devstack_stop.sh [--all]"
  exit 1
fi

case "${1:-}" in
  --status)
    say "服务状态："
    is_up "$OPIK_UI"  && ok "Opik     $OPIK_UI（compose 项目 $OPIK_PROJECT）" || fail "Opik     未运行"
    is_up "$JAEGER_UI" && ok "Jaeger   $JAEGER_UI（容器 $JAEGER_CONTAINER）"  || fail "Jaeger   未运行"
    netstat.exe -ano 2>/dev/null | grep -q ":$SERVE_PORT .*LISTENING" && ok "serve    ws://127.0.0.1:$SERVE_PORT" || fail "serve    未运行"
    netstat.exe -ano 2>/dev/null | grep -q ":$WEB_PORT .*LISTENING"   && ok "web      http://127.0.0.1:$WEB_PORT"   || fail "web      未运行"
    ;;
  *)
    say "一键启动 qi-agent 开发环境（$REPO）"
    cd "$REPO"
    rc=0
    start_opik    || rc=1
    start_jaeger  || rc=1
    start_serve   || rc=1
    start_web     || rc=1
    echo
    if [ "$rc" -eq 0 ]; then
      say "======== 环境就绪 ========"
    else
      fail "部分组件未就绪（见上方 ✗）——处理后重跑本脚本即可（幂等，已就绪的会跳过）"
    fi
    ok "评测平台 Opik:  $OPIK_UI"
    ok "调用链 Jaeger:  $JAEGER_UI（trace 经 web 同源反代 /jaeger 也可达）"
    ok "内核 serve:     ws://127.0.0.1:$SERVE_PORT"
    ok "Web 前端:       http://127.0.0.1:$WEB_PORT（token: $(cat "$WEB_TOKEN_FILE" 2>/dev/null | head -c 12)…）"
    ok "评测运行:       PYTHONPATH= uv run python -m evaluation.run --suite smoke"
    echo
    say "日志目录: $LOGS_DIR  |  停止: scripts/devstack_stop.sh（--all 连容器一起停）"
    ;;
esac
