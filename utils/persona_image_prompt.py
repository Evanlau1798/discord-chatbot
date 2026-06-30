from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.persona_store import Persona

PERSONA_IMAGE_DIR = Path("persona/imagen")
MAX_IMAGE_PROMPT_CHARS = 6000
IMAGE_TEXT_POLICY = (
    "Text policy: Do not include readable text, captions, labels, logos, signs, handwriting, "
    "letters, numbers, or UI text in the image unless the user explicitly asks for specific visible text."
)


class PersonaImagePromptStore:
    def __init__(self, image_dir: str | Path = PERSONA_IMAGE_DIR):
        self.image_dir = Path(image_dir)

    def get_prompt(self, persona: Persona | None) -> str:
        if persona is None:
            return ""
        matched = self._load_exact_match(persona)
        if matched is None:
            matched = self._load_metadata_match(persona)
        if matched is None:
            return ""
        return _format_image_prompt(matched)[:MAX_IMAGE_PROMPT_CHARS]

    def _load_exact_match(self, persona: Persona) -> dict[str, Any] | None:
        candidates = [_normalize_identifier(persona.key), _normalize_identifier(persona.name)]
        for candidate in candidates:
            if not candidate:
                continue
            payload = _load_json_file(self.image_dir / f"{candidate}.json")
            if payload is not None:
                return payload
        return None

    def _load_metadata_match(self, persona: Persona) -> dict[str, Any] | None:
        persona_ids = _collect_persona_identifiers(persona)
        if not persona_ids or not self.image_dir.exists():
            return None
        for path in sorted(self.image_dir.glob("*.json")):
            payload = _load_json_file(path)
            if payload is None:
                continue
            image_ids = _collect_image_prompt_identifiers(path, payload)
            if persona_ids & image_ids:
                return payload
        return None


def merge_persona_image_prompt(image_prompt: str, generated_prompt: str) -> str:
    normalized_image_prompt = str(image_prompt or "").strip()
    normalized_generated_prompt = str(generated_prompt or "").strip()
    if not normalized_image_prompt:
        return f"{IMAGE_TEXT_POLICY}\n\nUser image request:\n{normalized_generated_prompt}".strip()
    return (
        "Use the following character visual reference as stable identity constraints.\n"
        f"{normalized_image_prompt}\n\n"
        f"{IMAGE_TEXT_POLICY}\n\n"
        "User image request:\n"
        f"{normalized_generated_prompt}"
    ).strip()


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _collect_persona_identifiers(persona: Persona) -> set[str]:
    values = [persona.key, persona.name]
    data = persona.data if isinstance(persona.data, dict) else {}
    values.extend([
        data.get("characterName"),
        data.get("trueName"),
        data.get("name"),
        data.get("alias"),
    ])
    return {_normalize_identifier(value) for value in values if _normalize_identifier(value)}


def _collect_image_prompt_identifiers(path: Path, payload: dict[str, Any]) -> set[str]:
    values = [path.stem, payload.get("characterName"), payload.get("trueName"), payload.get("name"), payload.get("alias")]
    character = payload.get("character")
    if isinstance(character, dict):
        values.extend([character.get("name"), character.get("alias")])
    return {_normalize_identifier(value) for value in values if _normalize_identifier(value)}


def _normalize_identifier(value) -> str:
    return str(value or "").strip().lower()


def _format_image_prompt(payload: dict[str, Any]) -> str:
    return "\n".join(_format_value(key, value) for key, value in payload.items() if _format_value(key, value))


def _format_value(key: str, value) -> str:
    text = _stringify(value)
    return f"{key}: {text}" if text else ""


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return "; ".join(_format_value(str(key), item) for key, item in value.items() if _stringify(item))
    if isinstance(value, list):
        return "; ".join(_stringify(item) for item in value if _stringify(item))
    return str(value).strip()
