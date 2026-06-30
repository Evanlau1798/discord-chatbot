from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_PERSONA_KEY_ENV = "DEFAULT_PERSONA_KEY"
REPO_DEFAULT_PERSONA_KEY = "example"


def get_default_persona_key(env: Mapping[str, str] | None = None) -> str:
    values = env if env is not None else os.environ
    return str(values.get(DEFAULT_PERSONA_KEY_ENV, REPO_DEFAULT_PERSONA_KEY)).strip() or REPO_DEFAULT_PERSONA_KEY
