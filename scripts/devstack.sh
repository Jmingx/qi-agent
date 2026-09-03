#!/bin/bash
# devstack.sh —— qi-agent 开发环境一键启动/停止（可观测 + 评测 + 内核）
#
# 启动组件：
#   1. Opik      评测平台（docker compose——opik-selfhost）     UI http://127.0.0.1:5173
#   2. Jaeger    可观测 trace 后端（v1.66 all-in-one exe）        UI http://127.0.0.1:16686/jaeger
#   3. serve     内核 WS 服务（OTel 导出 → Jaeger）              ws://127.0.0.1:8771
#   4. web       Web Bridge + 前端（含 /jaeger 同源反代）        http://127.0.0.1:9004
#
# 用法：
#   ./devstack.sh            一键启动（幂等——已运行的服务跳过）
#   ./devstack.sh --stop     全部停止（含 docker compose down）
#   ./devstack.sh --status   查看各服务状态
#
# 设计（2026-09-03）：
#   - 幂等：pid 文件 + 端口预检，重复执行不双起
#   - 健壮：每个服务"等待就绪"（curl 重试 + 超时），失败不中断后续但汇总报告
#   - 日志：全部写 $DEVSTACK_DIR/logs/（排查用）
#   - 依赖：Docker Desktop（Opik 需要）、jaeger exe（首次自动下载提示）

set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEVSTACK_DIR="${TMPDIR:-/tmp}/devstack"      # pid/日志目录（Windows: $LOCALAPPDATA/Temp）
LOGS_DIR="$DEVSTACK_DIR/logs"
mkdir -p "$LOGS_DIR"

# ---------- 路径与常量（按本机实际情况） ----------
JAEGER_EXE="$HOME/AppData/Local/Temp/jaeger-1.66.0-windows-amd64/jaeger-all-in-one.exe"
CLOUDFLARED_EXE="$HOME/AppData/Local/Temp/cloudflared.exe"
OPIK_COMPOSE="$HOME/AppData/Local/Temp/opik-selfhost/deployment/docker-compose"
WEB_TOKEN_FILE="$HOME/.qi-agent/web_token"

SERVE_PORT=8771
WEB_PORT=9004
JAEGER_UI=http://127.0.0.1:16686/jaeger/
OPIK_UI=http://127.0.0.1:5173/

# ---------- 工具函数 ----------
say()  { printf '\033[1;34m[devstack]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; }
pid_file() { echo "$DEVSTACK_DIR/$1.pid"; }

is_up() {  # is_up <url> —— HTTP 200 即视为就绪
  curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null | grep -q '200'
}

wait_up() {  # wait_up <url> <名字> <超时秒>
  local url="$1" name="$2" timeout="${3:-60}" i
  for ((i = 0; i < timeout; i += 2)); do
    is_up "$url" && { ok "$name 就绪 ($url)"; return 0; }
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

stop_pid() {  # stop_pid <pid文件> <名字>
  local pf="$1" name="$2"
  if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
    kill "$(cat "$pf")" 2>/dev/null
    sleep 1
    kill -9 "$(cat "$pf")" 2>/dev/null   # 兜底强杀
    ok "$name 已停止"
  fi
  rm -f "$pf"
}

# ---------- 各组件启动 ----------
start_docker() {
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
  for ((i = 0; i < 90; i += 3)); do   # 最多等 4.5 分钟
    docker info >/dev/null 2>&1 && { ok "Docker daemon 就绪"; return 0; }
    sleep 3
  done
  fail "Docker daemon 启动超时"
  return 1
}

start_opik() {
  if is_up "$OPIK_UI"; then
    ok "Opik 已在运行 ($OPIK_UI)"
    return 0
  fi
  start_docker || return 1
  if [ ! -d "$OPIK_COMPOSE" ]; then
    fail "未找到 Opik compose：$OPIK_COMPOSE（首次需: git clone https://github.com/comet-ml/opik $HOME/AppData/Local/Temp/opik-selfhost）"
    return 1
  fi
  say "Opik docker compose 启动中…"
  (cd "$OPIK_COMPOSE" && docker compose up -d) > "$LOGS_DIR/opik.log" 2>&1 \
    || { fail "docker compose 失败（日志 $LOGS_DIR/opik.log）"; return 1; }
  wait_up "$OPIK_UI" "Opik" 180
}

start_jaeger() {
  local pf
  pf="$(pid_file jaeger)"
  if is_up "$JAEGER_UI"; then
    ok "Jaeger 已在运行 ($JAEGER_UI)"
    return 0
  fi
  if [ ! -f "$JAEGER_EXE" ]; then
    fail "未找到 jaeger-all-in-one.exe：$JAEGER_EXE"
    fail "下载: https://github.com/jaegertracing/jaeger/releases/download/v1.66.0/jaeger-1.66.0-windows-amd64.tar.gz"
    return 1
  fi
  start_bg jaeger "$pf" "$JAEGER_EXE" --query.base-path=/jaeger --collector.otlp.enabled=true
  wait_up "$JAEGER_UI" "Jaeger" 60
}

start_serve() {
  local pf
  pf="$(pid_file serve)"
  if proc_alive "$pf" || (netstat -ano 2>/dev/null | grep -q ":$SERVE_PORT .*LISTENING"); then
    ok "serve 已在运行 (ws://127.0.0.1:$SERVE_PORT)"
    return 0
  fi
  start_bg serve "$pf" env OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 PYTHONPATH= uv run python -m qi_agent.serve --port "$SERVE_PORT"
  # serve 就绪检查（WS 端口——用 TCP 探测）
  for ((i = 0; i < 30; i += 2)); do
    netstat -ano 2>/dev/null | grep -q ":$SERVE_PORT .*LISTENING" && { ok "serve 就绪 (ws://127.0.0.1:$SERVE_PORT)"; return 0; }
    sleep 2
  done
  fail "serve 未就绪——日志 $LOGS_DIR/serve.log"
  return 1
}

start_web() {
  local pf
  pf="$(pid_file web)"
  if proc_alive "$pf" || (netstat -ano 2>/dev/null | grep -q ":$WEB_PORT .*LISTENING"); then
    ok "web 已在运行 (http://127.0.0.1:$WEB_PORT)"
    return 0
  fi
  # 注意：web 必须在 serve 之后起（Bridge 不会自动重连 serve——2026-09-03 踩坑）
  start_bg web "$pf" env PYTHONPATH= uv run python -m qi_agent.web.server --port "$WEB_PORT" --serve "ws://127.0.0.1:$SERVE_PORT"
  wait_up "http://127.0.0.1:$WEB_PORT/" "web" 60
}

# ---------- 主流程 ----------
case "${1:-}" in
  --stop)
    say "停止全部服务…"
    stop_pid "$(pid_file web)" "web"
    stop_pid "$(pid_file serve)" "serve"
    stop_pid "$(pid_file jaeger)" "Jaeger"
    if docker info >/dev/null 2>&1 && [ -d "$OPIK_COMPOSE" ]; then
      (cd "$OPIK_COMPOSE" && docker compose down) >/dev/null 2>&1 && ok "Opik 容器已停止（数据保留在 volume）"
    fi
    say "全部停止完成"
    ;;
  --status)
    say "服务状态："
    is_up "$OPIK_UI" && ok "Opik     $OPIK_UI" || fail "Opik     未运行"
    is_up "$JAEGER_UI" && ok "Jaeger   $JAEGER_UI" || fail "Jaeger   未运行"
    netstat -ano 2>/dev/null | grep -q ":$SERVE_PORT .*LISTENING" && ok "serve    ws://127.0.0.1:$SERVE_PORT" || fail "serve    未运行"
    netstat -ano 2>/dev/null | grep -q ":$WEB_PORT .*LISTENING" && ok "web      http://127.0.0.1:$WEB_PORT" || fail "web      未运行"
    ;;
  *)
    say "一键启动 qi-agent 开发环境（$REPO）"
    cd "$REPO"
    start_opik
    start_jaeger
    start_serve
    start_web
    echo
    say "======== 环境就绪 ========"
    ok "评测平台 Opik:  $OPIK_UI"
    ok "调用链 Jaeger:  $JAEGER_UI（trace 经 web 同源反代 /jaeger 也可达）"
    ok "内核 serve:     ws://127.0.0.1:$SERVE_PORT"
    ok "Web 前端:       http://127.0.0.1:$WEB_PORT（token: $(cat "$WEB_TOKEN_FILE" 2>/dev/null | head -c 12)…）"
    ok "评测运行:       cd $REPO/scripts/eval && PYTHONPATH= uv run python run_eval.py"
    echo
    say "日志目录: $LOGS_DIR  |  停止: $0 --stop"
    ;;
esac
