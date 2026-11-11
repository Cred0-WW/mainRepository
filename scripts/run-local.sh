#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
PYTHON_BIN=${PYTHON_BIN:-python3}
NODE_BIN=${NODE_BIN:-node}

# This directory contains generated evidence only. Discard the preceding run so
# an early failure cannot accidentally reuse an old successful browser report.
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import shutil
directory = Path('artifacts')
directory.mkdir(exist_ok=True)
for entry in directory.iterdir():
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()
PY

npm_run() {
  if [ -n "${NPM_CLI_JS:-}" ]; then
    "$NODE_BIN" "$NPM_CLI_JS" "$@"
  elif command -v npm >/dev/null 2>&1; then
    npm "$@"
  else
    echo 'Не найден npm. Установи Node.js с npm или задай NPM_CLI_JS и NODE_BIN.' >&2
    return 1
  fi
}

finish() {
  status=$?
  trap - EXIT INT TERM
  if "$PYTHON_BIN" scripts/ci.py collect; then
    :
  else
    collection_status=$?
    if [ "$status" -eq 0 ]; then status=$collection_status; fi
  fi
  exit "$status"
}

trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ ! -f .env ]; then
  export DB_USER=${DB_USER:-app}
  export DB_NAME=${DB_NAME:-appdb}
  export APP_PORT=${APP_PORT:-8082}
  if [ -z "${DB_PASSWORD:-}" ]; then
    DB_PASSWORD=$(openssl rand -hex 24)
    export DB_PASSWORD
  fi
  "$PYTHON_BIN" scripts/ci.py create-env
fi

if ! docker info >/dev/null 2>&1; then
  echo 'Docker Desktop не готов. Открой его и повтори запуск.' >&2
  exit 1
fi

docker compose config -q
docker compose up -d --build --wait --wait-timeout 120
docker compose ps
BASE_URL="http://$(docker compose port app 8080)"
export BASE_URL

if [ -n "${NPM_CLI_JS:-}" ] || command -v npm >/dev/null 2>&1; then
  npm_run ci
elif [ ! -f node_modules/@playwright/test/cli.js ]; then
  echo 'Для установки зависимостей нужен npm. Установи Node.js с npm.' >&2
  exit 1
fi

# Use installed Chrome locally when available; Actions can install Chromium.
if [ -z "${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-}" ] && \
   [ -x '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' ]; then
  PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
elif [ -z "${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-}" ]; then
  "$NODE_BIN" node_modules/@playwright/test/cli.js install chromium
fi

"$NODE_BIN" node_modules/@playwright/test/cli.js test
