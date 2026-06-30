from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_IMAGINE_BASE_URL = "http://127.0.0.1:8890/v1"
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
FALSE_VALUES = {"", "0", "false", "no", "off", "disabled"}


def is_image_generation_enabled(env: Mapping[str, str] | None = None) -> bool:
    value = _env(env).get("AI_IMAGINE_ENABLED", "")
    return _env_bool(value, default=False)


def get_imagine_base_url(env: Mapping[str, str] | None = None) -> str:
    value = str(_env(env).get("AI_IMAGINE_BASE_URL") or "").strip()
    return value.rstrip("/") or DEFAULT_IMAGINE_BASE_URL


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _env_bool(value: str, *, default: bool) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default
