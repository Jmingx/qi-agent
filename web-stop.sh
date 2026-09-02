#!/usr/bin/env bash
# qi-agent Web Shell one-click stop (kill serve + web)
# Usage: bash web-stop.sh
set -uo pipefail

echo "============================================"
echo " qi-agent Web Shell stop"
echo "============================================"

KILLED=0

# Kill web app (port 9000)
for pid in $(netstat -ano 2>/dev/null | grep ":9000" | grep LISTENING | awk '{print $5}' | sort -u); do
  echo "[kill] web app PID $pid (port 9000)"
  MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /F >/dev/null 2>&1
  KILLED=1
done

# Kill kernel serve (port 8765)
for pid in $(netstat -ano 2>/dev/null | grep ":8765" | grep LISTENING | awk '{print $5}' | sort -u); do
  echo "[kill] kernel serve PID $pid (port 8765)"
  MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /F >/dev/null 2>&1
  KILLED=1
done

if [ "$KILLED" = "0" ]; then
  echo "No running qi-agent web processes found (ports 9000/8765 free)."
else
  echo ""
  echo "Done. Verify:"
  echo "  netstat -ano | grep -E ':9000|:8765' | grep LISTENING"
fi
