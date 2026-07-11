#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${ROOT_DIR}/reference/openserp"
CONTAINER_NAME="discord-chatbot-openserp"
IMAGE_NAME="discord-chatbot-openserp:local"
OPENSERP_PORT="${OPENSERP_PORT:-17000}"

if [ ! -f "${SOURCE_DIR}/Dockerfile" ] || [ ! -f "${SOURCE_DIR}/go.mod" ]; then
  echo "OpenSERP source is missing: ${SOURCE_DIR}" >&2
  exit 1
fi

container_runtime() {
  if command -v podman >/dev/null 2>&1; then
    printf '%s' podman
  elif command -v docker >/dev/null 2>&1; then
    printf '%s' docker
  else
    echo "Podman or Docker is required to run OpenSERP." >&2
    exit 1
  fi
}

wait_ready() {
  python - "http://127.0.0.1:${OPENSERP_PORT}" <<'PY'
import sys
import time
from urllib.request import urlopen

base_url = sys.argv[1]
deadline = time.time() + 90
last_error = ""
while time.time() < deadline:
    try:
        with urlopen(f"{base_url}/health", timeout=3) as response:
            if response.status == 200:
                print(f"OpenSERP is running: {base_url}")
                print(f"Bot env: OPENSERP_BASE_URL={base_url}")
                raise SystemExit(0)
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
print(f"OpenSERP did not become ready within 90 seconds: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

RUNTIME="$(container_runtime)"
if "${RUNTIME}" inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  if [ "$("${RUNTIME}" inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" = "true" ]; then
    wait_ready
    exit 0
  fi
  "${RUNTIME}" rm "${CONTAINER_NAME}" >/dev/null
fi

"${RUNTIME}" build --tag "${IMAGE_NAME}" "${SOURCE_DIR}"
"${RUNTIME}" run --detach \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --publish "127.0.0.1:${OPENSERP_PORT:-17000}:7000" \
  --env OPENSERP_SERVER_HOST=0.0.0.0 \
  --env OPENSERP_SERVER_PORT=7000 \
  --env OPENSERP_SERVER_INSECURE=false \
  --env OPENSERP_CORS_ENABLED=false \
  --env OPENSERP_PROXIES_ALLOW_REQUEST_PROXY_URL=false \
  --env OPENSERP_CAPTCHA_SOLVER_ENABLED=false \
  --env OPENSERP_RESILIENCE_MAX_RETRIES=0 \
  --env OPENSERP_GOOGLE_RATE_REQUESTS=60 \
  --env OPENSERP_GOOGLE_RATE_BURST=1 \
  "${IMAGE_NAME}" >/dev/null

wait_ready
