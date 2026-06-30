#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="${ROOT_DIR}/searxng-local"
ENV_FILE="${SERVICE_DIR}/.env"
ROOT_ENV_FILE="${ROOT_DIR}/.env"
SETTINGS_FILE="${SERVICE_DIR}/config/settings.yml"
COMPOSE_FILE="${SERVICE_DIR}/docker-compose.yml"
DEFAULT_PORT="19183"
PID_FILE="${SERVICE_DIR}/searxng.pid"
VENV_DIR="${SERVICE_DIR}/venv"
LOG_FILE="${ROOT_DIR}/tmp/searxng.log"
SEARXNG_SRC="${ROOT_DIR}/reference/searxng-master"

mkdir -p "${SERVICE_DIR}/config" "${SERVICE_DIR}/cache" "${ROOT_DIR}/tmp"

if [ ! -f "${ENV_FILE}" ]; then
  umask 077
  if command -v openssl >/dev/null 2>&1; then
    SECRET="$(openssl rand -hex 32)"
  else
    SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  {
    printf 'LOCAL_SEARXNG_PORT=%s\n' "${LOCAL_SEARXNG_PORT:-${DEFAULT_PORT}}"
    printf 'SEARXNG_SECRET=%s\n' "${SECRET}"
  } > "${ENV_FILE}"
fi

set -a
# shellcheck source=/dev/null
. "${ENV_FILE}"
set +a

SEARXNG_URL="http://127.0.0.1:${LOCAL_SEARXNG_PORT:-${DEFAULT_PORT}}"

render_settings() {
  (
    cd "${ROOT_DIR}"
    python -m utils.searxng_settings \
      --env-file "${ENV_FILE}" \
      --env-file "${ROOT_ENV_FILE}" \
      --output "${SETTINGS_FILE}"
  )
}

render_compose() {
  cat > "${COMPOSE_FILE}" <<'YAML'
name: discord-chatbot-searxng

services:
  searxng:
    container_name: discord-chatbot-searxng
    image: docker.io/searxng/searxng:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:${LOCAL_SEARXNG_PORT:-19183}:8080"
    environment:
      SEARXNG_BASE_URL: "http://127.0.0.1:${LOCAL_SEARXNG_PORT:-19183}/"
      SEARXNG_SECRET: "${SEARXNG_SECRET:?SEARXNG_SECRET is required}"
      SEARXNG_LIMITER: "false"
      SEARXNG_PUBLIC_INSTANCE: "false"
      SEARXNG_IMAGE_PROXY: "false"
      SEARXNG_METHOD: "GET"
    volumes:
      - ./config:/etc/searxng:rw
      - ./cache:/var/cache/searxng:rw
YAML
}

wait_ready() {
  python - "${SEARXNG_URL}" <<'PY'
import sys
import time
from urllib.request import Request, urlopen

base_url = sys.argv[1].rstrip("/")
deadline = time.time() + 60
last_error = ""
while time.time() < deadline:
    try:
        request = Request(f"{base_url}/config", headers={"X-Real-IP": "127.0.0.1"})
        with urlopen(request, timeout=3) as response:
            if response.status == 200:
                print(f"SearXNG is running: {base_url}")
                print(f"Bot env: SEARXNG_BASE_URL={base_url}")
                raise SystemExit(0)
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
print(f"SearXNG did not become ready within 60 seconds: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

docker_available() {
  command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1
}

install_venv() {
  if [ ! -x "${VENV_DIR}/bin/searxng-run" ]; then
    python3.12 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip
    "${VENV_DIR}/bin/python" -m pip install --upgrade setuptools wheel
    "${VENV_DIR}/bin/python" -m pip install \
      -r "${SEARXNG_SRC}/requirements.txt" \
      -r "${SEARXNG_SRC}/requirements-server.txt"
    "${VENV_DIR}/bin/python" -m pip install --no-build-isolation -e "${SEARXNG_SRC}"
  fi
  "${VENV_DIR}/bin/python" -m pip install --upgrade pysqlite3-binary
  site_dir="$("${VENV_DIR}/bin/python" - <<'PY'
import site

print(site.getsitepackages()[0])
PY
)"
  cat > "${site_dir}/sitecustomize.py" <<'PY'
import sys

try:
    import pysqlite3
except ImportError:
    pass
else:
    sys.modules["sqlite3"] = pysqlite3
PY
}

start_docker() {
  cd "${SERVICE_DIR}"
  docker compose up -d
  wait_ready
}

start_venv() {
  if [ -f "${PID_FILE}" ] && kill -0 "$(cat "${PID_FILE}")" >/dev/null 2>&1; then
    echo "SearXNG is already running: ${SEARXNG_URL}"
    return
  fi
  install_venv
  SEARXNG_SETTINGS_PATH="${SETTINGS_FILE}" \
  SEARXNG_PORT="${LOCAL_SEARXNG_PORT:-${DEFAULT_PORT}}" \
  SEARXNG_BIND_ADDRESS="127.0.0.1" \
  SEARXNG_SECRET="${SEARXNG_SECRET}" \
  SEARXNG_LIMITER="false" \
  SEARXNG_PUBLIC_INSTANCE="false" \
  SEARXNG_IMAGE_PROXY="false" \
  SEARXNG_METHOD="GET" \
    setsid "${VENV_DIR}/bin/granian" \
      --interface wsgi \
      --host "127.0.0.1" \
      --port "${LOCAL_SEARXNG_PORT:-${DEFAULT_PORT}}" \
      --workers 1 \
      --log-level warning \
      "searx.webapp:app" > "${LOG_FILE}" 2>&1 < /dev/null &
  echo "$!" > "${PID_FILE}"
  wait_ready
}

render_settings
render_compose

case "${SEARXNG_RUNTIME:-auto}" in
  docker)
    start_docker
    ;;
  venv)
    start_venv
    ;;
  auto)
    if docker_available; then
      start_docker
    else
      start_venv
    fi
    ;;
  *)
    echo "Unknown SEARXNG_RUNTIME: ${SEARXNG_RUNTIME}" >&2
    exit 1
    ;;
esac
