#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="${ROOT_DIR}/searxng-local"
PID_FILE="${SERVICE_DIR}/searxng.pid"
COMPOSE_FILE="${SERVICE_DIR}/docker-compose.yml"

stop_pid() {
  local pid="$1"
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    return
  fi
  kill "-${pid}" >/dev/null 2>&1 || kill "${pid}" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      return
    fi
    sleep 0.5
  done
  kill -9 "-${pid}" >/dev/null 2>&1 || kill -9 "${pid}" >/dev/null 2>&1 || true
}

if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}")"
  if [[ "${PID}" =~ ^[0-9]+$ ]]; then
    stop_pid "${PID}"
  fi
  rm -f "${PID_FILE}"
fi

if [ -f "${COMPOSE_FILE}" ] && command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
  cd "${SERVICE_DIR}"
  docker compose down
fi
