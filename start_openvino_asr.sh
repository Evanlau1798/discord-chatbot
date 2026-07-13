#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="discord-chatbot-openvino-asr"
IMAGE_NAME="discord-chatbot-openvino-asr:local"
CONTAINERFILE="${ROOT_DIR}/services/openvino_asr/Containerfile"

load_env_file() {
  local env_file="$1" line key value
  [ -f "$env_file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ -v "$key" ] && continue
    if [[ "$value" =~ ^\".*\"$ || "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
    fi
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$env_file"
}

container_runtime() {
  if command -v podman >/dev/null 2>&1; then
    printf '%s' podman
  elif command -v docker >/dev/null 2>&1; then
    printf '%s' docker
  else
    echo "Podman or Docker is required to run the OpenVINO ASR service." >&2
    exit 1
  fi
}

is_enabled() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    0|false|no|off) return 1 ;;
    *) echo "LOCAL_ASR_ENABLED must be a boolean." >&2; exit 1 ;;
  esac
}

validate_integer() {
  local name="$1" value="$2" minimum="$3" maximum="$4"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || (( value < minimum || value > maximum )); then
    echo "${name} is outside the allowed range." >&2
    exit 1
  fi
}

resolve_storage_path() {
  local value="$1" candidate
  if [[ "$value" = /* ]]; then
    candidate="$(realpath -m -- "$value")"
  else
    candidate="$(realpath -m -- "${ROOT_DIR}/${value}")"
  fi
  case "$candidate" in
    "${ROOT_DIR}/tmp"/*) printf '%s' "$candidate" ;;
    *) echo "ASR model and cache paths must stay under ${ROOT_DIR}/tmp." >&2; exit 1 ;;
  esac
}

wait_ready() {
  python - "http://127.0.0.1:${LOCAL_ASR_PORT}" "$LOCAL_ASR_READY_TIMEOUT_SECONDS" <<'PY'
import json
import sys
import time
from urllib.request import urlopen

base_url = sys.argv[1]
deadline = time.monotonic() + int(sys.argv[2])
last_error = ""
while time.monotonic() < deadline:
    try:
        with urlopen(f"{base_url}/health/ready", timeout=3) as response:
            payload = json.load(response)
            if response.status == 200 and payload.get("ready") is True and payload.get("device", "").startswith("GPU"):
                print(f"OpenVINO ASR is ready: {base_url}")
                raise SystemExit(0)
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
print(f"OpenVINO ASR did not become ready: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

load_env_file "${ROOT_DIR}/.env"

LOCAL_ASR_ENABLED="${LOCAL_ASR_ENABLED:-1}"
LOCAL_ASR_PORT="${LOCAL_ASR_PORT:-18765}"
LOCAL_ASR_MAX_CONCURRENCY="${LOCAL_ASR_MAX_CONCURRENCY:-1}"
LOCAL_ASR_MAX_DURATION_SECONDS="${LOCAL_ASR_MAX_DURATION_SECONDS:-60}"
LOCAL_ASR_MAX_QUEUE_SIZE="${LOCAL_ASR_MAX_QUEUE_SIZE:-2}"
LOCAL_ASR_QUEUE_TIMEOUT_SECONDS="${LOCAL_ASR_QUEUE_TIMEOUT_SECONDS:-20}"
LOCAL_ASR_REQUEST_TIMEOUT_SECONDS="${LOCAL_ASR_REQUEST_TIMEOUT_SECONDS:-45}"
LOCAL_ASR_MAX_UPLOAD_BYTES="${LOCAL_ASR_MAX_UPLOAD_BYTES:-52428800}"
LOCAL_ASR_DEVICE="${LOCAL_ASR_DEVICE:-GPU}"
LOCAL_ASR_MODEL_ID="${LOCAL_ASR_MODEL_ID:-OpenVINO/whisper-small-fp16-ov}"
LOCAL_ASR_MODEL_REVISION="${LOCAL_ASR_MODEL_REVISION:-2410d022171ca8a97343182f88eec8807a324db9}"
LOCAL_ASR_MODEL_DIR="${LOCAL_ASR_MODEL_DIR:-./tmp/openvino-asr/models/whisper-small-fp16-ov}"
LOCAL_ASR_CACHE_DIR="${LOCAL_ASR_CACHE_DIR:-./tmp/openvino-asr/cache}"
LOCAL_ASR_HOTWORDS_ENABLED="${LOCAL_ASR_HOTWORDS_ENABLED:-0}"
LOCAL_ASR_MAX_HOTWORDS="${LOCAL_ASR_MAX_HOTWORDS:-8}"
LOCAL_ASR_READY_TIMEOUT_SECONDS="${LOCAL_ASR_READY_TIMEOUT_SECONDS:-600}"

if ! is_enabled "$LOCAL_ASR_ENABLED"; then
  "${ROOT_DIR}/stop_openvino_asr.sh"
  echo "OpenVINO ASR is disabled by LOCAL_ASR_ENABLED."
  exit 0
fi

validate_integer LOCAL_ASR_PORT "$LOCAL_ASR_PORT" 1024 65535
validate_integer LOCAL_ASR_READY_TIMEOUT_SECONDS "$LOCAL_ASR_READY_TIMEOUT_SECONDS" 1 3600
if [[ "${LOCAL_ASR_DEVICE^^}" != GPU* ]]; then
  echo "LOCAL_ASR_DEVICE must explicitly select GPU." >&2
  exit 1
fi
if [ ! -r /dev/dri/renderD128 ] || [ ! -w /dev/dri/renderD128 ]; then
  echo "Intel GPU render node /dev/dri/renderD128 is not accessible." >&2
  exit 1
fi
if [ ! -f "$CONTAINERFILE" ]; then
  echo "OpenVINO ASR Containerfile is missing: ${CONTAINERFILE}" >&2
  exit 1
fi

MODEL_DIR="$(resolve_storage_path "$LOCAL_ASR_MODEL_DIR")"
CACHE_DIR="$(resolve_storage_path "$LOCAL_ASR_CACHE_DIR")"
mkdir -p "$MODEL_DIR" "$CACHE_DIR"

RUNTIME="$(container_runtime)"
"${ROOT_DIR}/stop_openvino_asr.sh"

python - "$LOCAL_ASR_PORT" <<'PY'
import socket
import sys

with socket.socket() as sock:
    try:
        sock.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError as exc:
        print(f"LOCAL_ASR_PORT is already in use: {exc}", file=sys.stderr)
        raise SystemExit(1)
PY

"${RUNTIME}" build --tag "$IMAGE_NAME" --file "$CONTAINERFILE" "$ROOT_DIR"

VOLUME_LABEL=""
RUNTIME_USER_ARGS=(--user "$(id -u):$(id -g)")
if [ "$RUNTIME" = "podman" ]; then
  VOLUME_LABEL=":Z"
  RUNTIME_USER_ARGS=(--userns keep-id --user "$(id -u):$(id -g)" --group-add keep-groups)
fi

if ! "${RUNTIME}" run --detach \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --publish "127.0.0.1:${LOCAL_ASR_PORT}:${LOCAL_ASR_PORT}" \
  --device /dev/dri/renderD128 \
  "${RUNTIME_USER_ARGS[@]}" \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --volume "${MODEL_DIR}:/var/lib/openvino-asr/models/whisper-small-fp16-ov${VOLUME_LABEL}" \
  --volume "${CACHE_DIR}:/var/lib/openvino-asr/cache${VOLUME_LABEL}" \
  --env "LOCAL_ASR_ENABLED=1" \
  --env "LOCAL_ASR_PORT=${LOCAL_ASR_PORT}" \
  --env "LOCAL_ASR_MAX_CONCURRENCY=${LOCAL_ASR_MAX_CONCURRENCY}" \
  --env "LOCAL_ASR_MAX_DURATION_SECONDS=${LOCAL_ASR_MAX_DURATION_SECONDS}" \
  --env "LOCAL_ASR_MAX_QUEUE_SIZE=${LOCAL_ASR_MAX_QUEUE_SIZE}" \
  --env "LOCAL_ASR_QUEUE_TIMEOUT_SECONDS=${LOCAL_ASR_QUEUE_TIMEOUT_SECONDS}" \
  --env "LOCAL_ASR_REQUEST_TIMEOUT_SECONDS=${LOCAL_ASR_REQUEST_TIMEOUT_SECONDS}" \
  --env "LOCAL_ASR_MAX_UPLOAD_BYTES=${LOCAL_ASR_MAX_UPLOAD_BYTES}" \
  --env "LOCAL_ASR_DEVICE=${LOCAL_ASR_DEVICE}" \
  --env "LOCAL_ASR_MODEL_ID=${LOCAL_ASR_MODEL_ID}" \
  --env "LOCAL_ASR_MODEL_REVISION=${LOCAL_ASR_MODEL_REVISION}" \
  --env "LOCAL_ASR_HOTWORDS_ENABLED=${LOCAL_ASR_HOTWORDS_ENABLED}" \
  --env "LOCAL_ASR_MAX_HOTWORDS=${LOCAL_ASR_MAX_HOTWORDS}" \
  --env "HOME=/tmp/openvino-home" \
  --env "HF_HOME=/var/lib/openvino-asr/models/.hf-cache" \
  --env "XDG_CACHE_HOME=/var/lib/openvino-asr/cache/xdg" \
  "$IMAGE_NAME" >/dev/null; then
  echo "Failed to start OpenVINO ASR container." >&2
  exit 1
fi

if ! wait_ready; then
  "${RUNTIME}" logs --tail 40 "$CONTAINER_NAME" >&2 || true
  "${ROOT_DIR}/stop_openvino_asr.sh" || true
  exit 1
fi
