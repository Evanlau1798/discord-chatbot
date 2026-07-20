#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="discord-chatbot-openserp"
OPENSERP_RELEASE="0.8.6"
OPENSERP_IMAGE_DIGEST="sha256:ac2156fc91d0174623198fb7b1b4766bd55f7c7838076224365b270af385f9e7"
IMAGE_REFERENCE="docker.io/karust/openserp@${OPENSERP_IMAGE_DIGEST}"
RELEASE_LABEL="io.discord-chatbot.openserp.release"
DIGEST_LABEL="io.discord-chatbot.openserp.digest"
OPENSERP_PORT="${OPENSERP_PORT:-17000}"

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

container_uses_pinned_release() {
  local release digest
  release="$("${RUNTIME}" inspect -f '{{ index .Config.Labels "io.discord-chatbot.openserp.release" }}' "${CONTAINER_NAME}")"
  digest="$("${RUNTIME}" inspect -f '{{ index .Config.Labels "io.discord-chatbot.openserp.digest" }}' "${CONTAINER_NAME}")"
  [ "${release}" = "${OPENSERP_RELEASE}" ] && [ "${digest}" = "${OPENSERP_IMAGE_DIGEST}" ]
}

remove_existing_container() {
  if [ "$("${RUNTIME}" inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" = "true" ]; then
    "${RUNTIME}" stop "${CONTAINER_NAME}" >/dev/null
  fi
  "${RUNTIME}" rm "${CONTAINER_NAME}" >/dev/null
}

RUNTIME="$(container_runtime)"
if "${RUNTIME}" inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  if container_uses_pinned_release \
    && [ "$("${RUNTIME}" inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" = "true" ]; then
    wait_ready
    exit 0
  fi
  echo "Replacing OpenSERP container with pinned stable release ${OPENSERP_RELEASE}."
  remove_existing_container
fi

if ! "${RUNTIME}" image inspect "${IMAGE_REFERENCE}" >/dev/null 2>&1; then
  "${RUNTIME}" pull "${IMAGE_REFERENCE}" >/dev/null
fi

"${RUNTIME}" run --detach \
  --pull never \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --label "${RELEASE_LABEL}=${OPENSERP_RELEASE}" \
  --label "${DIGEST_LABEL}=${OPENSERP_IMAGE_DIGEST}" \
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
  "${IMAGE_REFERENCE}" serve >/dev/null

wait_ready
