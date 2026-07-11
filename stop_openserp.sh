#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="discord-chatbot-openserp"

if command -v podman >/dev/null 2>&1; then
  RUNTIME=podman
elif command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
else
  echo "OpenSERP container runtime is not installed; nothing to stop."
  exit 0
fi

if "${RUNTIME}" inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  "${RUNTIME}" stop "${CONTAINER_NAME}" >/dev/null
  "${RUNTIME}" rm "${CONTAINER_NAME}" >/dev/null
  echo "Stopped ${CONTAINER_NAME}."
else
  echo "${CONTAINER_NAME} is not running."
fi
