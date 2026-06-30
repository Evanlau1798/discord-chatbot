from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AI_API_LOGGER_NAME = "discord.ai_api"


def log_ai_api_event(event_type: str, /, **fields: Any) -> None:
    payload = {"logged_at": datetime.now(timezone.utc).isoformat(), "event": event_type}
    payload.update({key: _serialize_for_log(value) for key, value in fields.items()})
    logging.getLogger(AI_API_LOGGER_NAME).info(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _serialize_for_log(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _serialize_for_log(asdict(value))
    if isinstance(value, dict):
        return {str(key): _serialize_for_log(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_for_log(item) for item in value]
    return repr(value)
