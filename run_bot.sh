#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="discord-chatbot"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_DIR/tmp"
LOG_FILE="$LOG_DIR/bot.log"
ENV_FILE="$PROJECT_DIR/.env"
MEMORY_KEY_SETUP_SCRIPT="$PROJECT_DIR/utils/memory_key_setup.py"
START_OPENSERP_SCRIPT="$PROJECT_DIR/start_openserp.sh"
STOP_OPENSERP_SCRIPT="$PROJECT_DIR/stop_openserp.sh"
START_OPENVINO_ASR_SCRIPT="$PROJECT_DIR/start_openvino_asr.sh"
STOP_OPENVINO_ASR_SCRIPT="$PROJECT_DIR/stop_openvino_asr.sh"
BOT_STOP_TIMEOUT_SECONDS="${BOT_STOP_TIMEOUT_SECONDS:-15}"
BOT_READY_TIMEOUT_SECONDS="${BOT_READY_TIMEOUT_SECONDS:-60}"

if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python"
fi

mkdir -p "$LOG_DIR"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed or not available in PATH." >&2
  exit 1
fi

if [ ! -x "$START_OPENSERP_SCRIPT" ] || [ ! -x "$STOP_OPENSERP_SCRIPT" ] \
  || [ ! -x "$START_OPENVINO_ASR_SCRIPT" ] || [ ! -x "$STOP_OPENVINO_ASR_SCRIPT" ]; then
  echo "Local service start/stop scripts are missing or not executable." >&2
  exit 1
fi

if [[ ! "$BOT_STOP_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [[ ! "$BOT_READY_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "BOT_STOP_TIMEOUT_SECONDS and BOT_READY_TIMEOUT_SECONDS must be non-negative integers." >&2
  exit 1
fi

ensure_memory_encryption_key() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE. Copy .env.example to .env and configure the required service credentials first." >&2
    exit 1
  fi
  if [ ! -f "$MEMORY_KEY_SETUP_SCRIPT" ]; then
    echo "Memory encryption key setup helper is missing: $MEMORY_KEY_SETUP_SCRIPT" >&2
    exit 1
  fi

  local key_state
  if ! key_state="$("$PYTHON_BIN" "$MEMORY_KEY_SETUP_SCRIPT" inspect "$ENV_FILE")"; then
    echo "Unable to inspect MEMORY_ENCRYPTION_KEY in $ENV_FILE." >&2
    exit 1
  fi
  case "$key_state" in
    valid)
      return
      ;;
    invalid)
      echo "MEMORY_ENCRYPTION_KEY is not a valid Fernet key. It was not overwritten because doing so could make existing encrypted memories unreadable." >&2
      echo "Restore the original key or generate a key manually before starting the bot." >&2
      exit 1
      ;;
    duplicate)
      echo "MEMORY_ENCRYPTION_KEY is defined more than once in $ENV_FILE. Keep exactly one definition before starting the bot." >&2
      exit 1
      ;;
    blank|missing)
      ;;
    *)
      echo "Unexpected MEMORY_ENCRYPTION_KEY inspection result: $key_state" >&2
      exit 1
      ;;
  esac

  if [ ! -t 0 ]; then
    echo "MEMORY_ENCRYPTION_KEY is blank or missing. Run ./run_bot.sh interactively to generate it, or set it manually." >&2
    exit 1
  fi
  while true; do
    printf "MEMORY_ENCRYPTION_KEY is blank or missing. Generate and save a secure key to .env now? [Y/n] " >&2
    local answer
    if ! IFS= read -r answer; then
      echo >&2
      echo "No response received; bot startup cancelled." >&2
      exit 1
    fi
    case "$answer" in
      ""|y|Y|yes|YES|Yes)
        if ! "$PYTHON_BIN" "$MEMORY_KEY_SETUP_SCRIPT" generate "$ENV_FILE" >/dev/null; then
          echo "Unable to generate MEMORY_ENCRYPTION_KEY; bot startup cancelled." >&2
          exit 1
        fi
        echo "Generated MEMORY_ENCRYPTION_KEY and saved it to .env without displaying the key."
        return
        ;;
      n|N|no|NO|No)
        echo "Bot startup cancelled. Set MEMORY_ENCRYPTION_KEY before trying again." >&2
        exit 1
        ;;
      *)
        echo "Please answer yes or no." >&2
        ;;
    esac
  done
}

stop_bot() {
  if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Bot tmux session '$SESSION_NAME' is not running."
    return
  fi

  echo "Gracefully stopping tmux session '$SESSION_NAME' with Ctrl+C..."
  if ! tmux send-keys -t "$SESSION_NAME" C-c; then
    echo "Unable to send Ctrl+C; force-closing tmux session '$SESSION_NAME'." >&2
    tmux kill-session -t "$SESSION_NAME"
    return
  fi

  local deadline=$((SECONDS + BOT_STOP_TIMEOUT_SECONDS))
  while tmux has-session -t "$SESSION_NAME" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "Bot did not stop within ${BOT_STOP_TIMEOUT_SECONDS}s; force-closing tmux session." >&2
      tmux kill-session -t "$SESSION_NAME"
      return
    fi
    sleep 0.25
  done
  echo "Bot stopped gracefully."
}

ensure_memory_encryption_key
stop_bot

echo "Stopping existing OpenVINO ASR service..."
"$STOP_OPENVINO_ASR_SCRIPT"

echo "Stopping existing OpenSERP service..."
"$STOP_OPENSERP_SCRIPT"

echo "Starting OpenSERP service..."
"$START_OPENSERP_SCRIPT"

LOG_OFFSET=0
if [ -f "$LOG_FILE" ]; then
  LOG_OFFSET="$(wc -c < "$LOG_FILE")"
fi

if ! tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_DIR" \
  "exec env PYTHONUNBUFFERED=1 \"$PYTHON_BIN\" main.py 2>&1 | tee -a \"$LOG_FILE\""; then
  echo "Failed to start bot; stopping local companion services to avoid leaving orphans." >&2
  "$STOP_OPENVINO_ASR_SCRIPT" || true
  "$STOP_OPENSERP_SCRIPT" || true
  exit 1
fi

READY_DEADLINE=$((SECONDS + BOT_READY_TIMEOUT_SECONDS))
BOT_READY=0
while tmux has-session -t "$SESSION_NAME" 2>/dev/null; do
  if tail -c "+$((LOG_OFFSET + 1))" "$LOG_FILE" 2>/dev/null | grep -Fq "Bot ready:"; then
    BOT_READY=1
    break
  fi
  if (( SECONDS >= READY_DEADLINE )); then
    break
  fi
  sleep 0.25
done

if [ "$BOT_READY" -ne 1 ]; then
  echo "Bot did not reach Discord Gateway ready within ${BOT_READY_TIMEOUT_SECONDS}s. Check: $LOG_FILE" >&2
  stop_bot
  "$STOP_OPENVINO_ASR_SCRIPT" || true
  "$STOP_OPENSERP_SCRIPT" || true
  exit 1
fi

echo "Starting OpenVINO ASR service after Discord Gateway is ready..."
if ! "$START_OPENVINO_ASR_SCRIPT"; then
  echo "OpenVINO ASR did not become ready; bot will continue with media fallback mode." >&2
fi

echo "Started bot in tmux session '$SESSION_NAME'."
echo "Attach with: tmux attach -t $SESSION_NAME"
echo "Logs: $LOG_FILE"
