#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

prepare_env() {
  if [ ! -f .env ]; then
    (umask 077
      password=$(openssl rand -hex 24)
      printf 'DB_USER=app\nDB_PASSWORD=%s\nDB_NAME=appdb\nAPP_PORT=8082\nCOMPOSE_PROJECT_NAME=githubactionslesson\n' "$password" > .env
    )
    echo 'Созданы локальные настройки в .env.'
  fi
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo 'Не найден Docker Desktop. Установи его в папку Программы.' >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo 'Запускается Docker Desktop…'
    open '/Applications/Docker.app'
    count=0
    until docker info >/dev/null 2>&1; do
      count=$((count + 1))
      if [ "$count" -ge 45 ]; then
        echo 'Docker ещё не готов. Открой Docker Desktop и повтори запуск.' >&2
        exit 1
      fi
      sleep 2
    done
  fi
}

app_url() {
  address=$(docker compose port app 8080)
  if [ -z "$address" ]; then
    echo 'Приложение не запущено. Выполни задачу «Проект: запустить».' >&2
    exit 1
  fi
  printf 'http://%s' "$address"
}

check_app() {
  url=$(app_url)
  docker compose ps
  echo
  echo "Проверка готовности: $url/healthz"
  result=$(curl --fail --silent --show-error --max-time 10 "$url/healthz")
  if [ "$result" != ok ]; then
    echo 'Приложение не прошло проверку готовности.' >&2
    exit 1
  fi
  echo "$result"
  echo 'Записи из PostgreSQL:'
  curl --fail --silent --show-error --max-time 10 "$url/api/items"
  echo
  echo "Страница: $url"
}

prepare_env
ensure_docker
case "${1:-start}" in
  start)
    docker compose config -q
    docker compose up -d --build --wait --wait-timeout 120
    check_app
    ;;
  open) open "$(app_url)" ;;
  check) check_app ;;
  logs) docker compose logs --follow --tail 50 app db ;;
  stop) docker compose stop ;;
  *) echo 'Доступные действия: start, open, check, logs, stop' >&2; exit 2 ;;
esac
