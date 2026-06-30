from __future__ import annotations

import os
import pickle

DEFAULT_SETTINGS_PATH = "./databases/AI_user_choice.pickle"
DEFAULT_PERSONA_KEY = "akira"
UNSET = object()


class AiChatUserSettingsStore:
    def __init__(self, path: str = DEFAULT_SETTINGS_PATH):
        self.path = path
        self.choices: dict[str, dict] = self.load()
        self.last_modified_time = self.get_file_last_modified_time()

    def get(self, user=None, user_id=None) -> dict:
        target_user_id = self._resolve_user_id(user=user, user_id=user_id)
        current_modified_time = self.get_file_last_modified_time()
        if current_modified_time != self.last_modified_time:
            self.choices = self.load()
            self.last_modified_time = current_modified_time
        return self._normalize_setting(self.choices.get(target_user_id))

    def get_persona(self, user=None, user_id=None) -> str | None:
        return self.get(user=user, user_id=user_id)["persona"]

    def modify(self, *, user=None, user_id=None, persona=UNSET) -> dict:
        target_user_id = self._resolve_user_id(user=user, user_id=user_id)
        setting = self.get(user_id=target_user_id)
        if persona is not UNSET:
            setting["persona"] = _normalize_persona(persona)
        self.choices[target_user_id] = setting
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "wb") as file:
            pickle.dump(self.choices, file)
        self.last_modified_time = self.get_file_last_modified_time()
        return setting

    def load(self) -> dict[str, dict]:
        try:
            with open(self.path, "rb") as file:
                raw_choices = pickle.load(file)
        except (FileNotFoundError, EOFError):
            return {}
        if not isinstance(raw_choices, dict):
            return {}
        return {str(user_id): self._normalize_setting(setting) for user_id, setting in raw_choices.items()}

    def get_file_last_modified_time(self) -> float:
        try:
            return os.path.getmtime(self.path)
        except FileNotFoundError:
            return 0.0

    @staticmethod
    def _resolve_user_id(user=None, user_id=None) -> str:
        if user is None and user_id is None:
            raise ValueError("必須至少指定一個 Discord user 或 user id")
        return str(user_id if user is None else user.id)

    @staticmethod
    def _normalize_setting(setting) -> dict:
        if isinstance(setting, bool):
            return {"persona": DEFAULT_PERSONA_KEY}
        if not isinstance(setting, dict):
            return {"persona": DEFAULT_PERSONA_KEY}
        return {"persona": _normalize_persona(setting.get("persona")) or DEFAULT_PERSONA_KEY}


def _normalize_persona(persona) -> str | None:
    text = str(persona or "").strip()
    return text or None
