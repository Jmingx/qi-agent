#!/usr/bin/env bash
# qi-agent Web Shell one-click start (serve + web, no browser auto-open)
# Usage: bash web-start.sh [serve_port] [web_port]
set -euo pipefail

SERVE_PORT="${1:-8765}"
WEB_PORT="${2:-9000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "============================================"
echo " qi-agent Web Shell start"
echo " serve port: $SERVE_PORT  |  web port: $WEB_PORT"
echo "============================================"

if [ ! -f "qi_agent/web/frontend/dist/index.html" ]; then
  echo "[WARN] Frontend not built! Run:"
  echo "  cd qi_agent/web/frontend && npm install && npm run build"
fi

echo "[1/2] Starting kernel serve (ws://127.0.0.1:$SERVE_PORT) ..."
uv run python -m qi_agent.serve --port "$SERVE_PORT" &
SERVE_PID=$!

sleep 2

echo "[2/2] Starting web app (http://127.0.0.1:$WEB_PORT) ..."
uv run python -m qi_agent.web.server --port "$WEB_PORT" &
WEB_PID=$!

echo ""
echo "Started! Open http://127.0.0.1:$WEB_PORT in your browser (not auto-opened)"
echo "Press Ctrl+C to stop both."

trap 'kill $SERVE_PID $WEB_PID 2>/dev/null' EXIT
wait
