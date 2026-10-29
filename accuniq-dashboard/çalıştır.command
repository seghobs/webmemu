#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
TASK_ROOT="$PWD"
TASK_LOG="$TASK_ROOT/backend/data"
mkdir -p "$TASK_LOG"
trap 'echo "Başlatma tamamlanamadı. Yukarıdaki hatayı ve backend/data kayıtlarını inceleyin."; read -r -p "Kapatmak için Enter: " || true' ERR

command -v python3 >/dev/null || { echo 'Python 3.10 veya üzerini kurun.'; exit 1; }
command -v node >/dev/null || { echo 'Node.js 20.9 veya üzerini kurun.'; exit 1; }
command -v npm >/dev/null || { echo 'npm bulunamadı. Node.js kurulumunu kontrol edin.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ gerekiyor"'
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<20 || (a===20 && b<9)) throw Error("Node.js 20.9+ gerekiyor")'

if curl -fsS --max-time 3 http://127.0.0.1:3001/ | grep -q 'ACCUNIQ Studio' &&
   curl -fsS --max-time 3 http://127.0.0.1:8010/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("status")=="ok" and d.get("device_model")=="BC380" else 1)'; then
    open 'http://127.0.0.1:3001/'
    echo 'ACCUNIQ Studio zaten çalışıyor.'
    exit 0
fi
for TASK_PORT in 3001 8010; do
    if lsof -nP -iTCP:"$TASK_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "$TASK_PORT portu kullanımda. Bu portu kullanan programı kapatıp tekrar deneyin."
        exit 1
    fi
done

# Windows bağımlılıklarını kullanmadan macOS'a özel kurulum oluştur.
TASK_ENV="$TASK_ROOT/.venv-macos"
if [ ! -x "$TASK_ENV/bin/python" ]; then python3 -m venv "$TASK_ENV"; fi
"$TASK_ENV/bin/python" -m pip install -r backend/requirements.txt
cd "$TASK_ROOT/frontend"
TASK_PLATFORM="$(uname -s)-$(uname -m)-$(node -p 'process.versions.node')"
if [ ! -f node_modules/.accuniq-platform ] || [ "$(cat node_modules/.accuniq-platform)" != "$TASK_PLATFORM" ]; then
    npm ci --no-fund --no-audit
    printf '%s' "$TASK_PLATFORM" > node_modules/.accuniq-platform
    TASK_REBUILD=1
else
    TASK_REBUILD=0
fi
if [ ! -f .next/BUILD_ID ]; then TASK_REBUILD=1; else
    if [ -n "$(find app package.json package-lock.json next.config.ts -type f -newer .next/BUILD_ID -print -quit)" ]; then TASK_REBUILD=1; fi
fi
if [ "$TASK_REBUILD" = 1 ]; then npm run build; fi

cd "$TASK_ROOT"
nohup "$TASK_ENV/bin/python" -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8010 > "$TASK_LOG/backend-out.log" 2> "$TASK_LOG/backend-error.log" < /dev/null &
TASK_BACKEND_PID=$!
cd "$TASK_ROOT/frontend"
nohup node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3001 > "$TASK_LOG/frontend-out.log" 2> "$TASK_LOG/frontend-error.log" < /dev/null &
TASK_FRONTEND_PID=$!
printf '{"backend":%s,"frontend":%s}\n' "$TASK_BACKEND_PID" "$TASK_FRONTEND_PID" > "$TASK_LOG/launch-state-macos.json"
TASK_READY=0
for TASK_ATTEMPT in {1..30}; do
    if curl -fsS --max-time 2 http://127.0.0.1:8010/api/health >/dev/null 2>&1 &&
       curl -fsS --max-time 2 http://127.0.0.1:3001/ >/dev/null 2>&1; then TASK_READY=1; break; fi
    if ! kill -0 "$TASK_BACKEND_PID" 2>/dev/null || ! kill -0 "$TASK_FRONTEND_PID" 2>/dev/null; then break; fi
    sleep 1
done
if [ "$TASK_READY" != 1 ]; then
    kill "$TASK_BACKEND_PID" "$TASK_FRONTEND_PID" 2>/dev/null || true
    echo "Servisler hazır olmadı. Kayıtlar: $TASK_LOG"
    exit 1
fi
open 'http://127.0.0.1:3001/'
echo 'ACCUNIQ Studio başlatıldı: http://127.0.0.1:3001/'
