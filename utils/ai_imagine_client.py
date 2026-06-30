from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import requests

from utils.ai_api_logging import log_ai_api_event
from utils.imagine_config import DEFAULT_IMAGINE_BASE_URL

LOCAL_SUB2API_BASE_URL = DEFAULT_IMAGINE_BASE_URL


class ImagineAPIError(Exception):
    pass


@dataclass(frozen=True)
class ImagineResult:
    prompt: str
    image_urls: list[str]
    image_paths: list[Path]


class ImagineClient:
    def __init__(self, api_key: str, base_url: str | None, model: str, download_dir: str | Path = "imagine-tmp", api_mode: str | None = None):
        if not api_key:
            raise ImagineAPIError("缺少 AI_IMAGINE_API_KEY")
        if not model:
            raise ImagineAPIError("缺少 AI_IMAGINE_MODEL")
        self.api_key = api_key
        self.base_url = (base_url or LOCAL_SUB2API_BASE_URL).rstrip("/")
        self.model = model
        self.download_dir = Path(download_dir)
        self.resolved_api_mode = _normalize_imagine_api_mode(api_mode)

    def generate(self, prompt: str) -> ImagineResult:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ImagineAPIError("生圖 prompt 不可為空")
        payload = {"model": self.model, "prompt": normalized_prompt}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        endpoint = f"{self.base_url}/images/generations"
        started_at = time.monotonic()
        log_ai_api_event(
            "request",
            provider="imagine",
            operation="images_generations",
            model=self.model,
            request_body={"model": self.model, "prompt_len": len(normalized_prompt)},
            request_meta={"url": endpoint, "api_mode": self.resolved_api_mode},
        )
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=(10, 180))
        except requests.RequestException as exc:
            _log_imagine_error("images_generations", self.model, started_at, exc)
            raise ImagineAPIError(f"Imagine API 請求失敗: {exc}") from exc

        try:
            _raise_imagine_http_error(response)
            response_payload = _load_response_json(response)
        except ImagineAPIError as exc:
            _log_imagine_error(
                "images_generations",
                self.model,
                started_at,
                exc,
                status_code=getattr(response, "status_code", None),
            )
            raise
        finally:
            response.close()
        entries = _extract_generated_image_entries(response_payload)
        if not entries:
            raise ImagineAPIError("Imagine API 未返回任何圖片資料")
        image_urls, image_paths = self._materialize_image_entries(entries)
        log_ai_api_event(
            "response",
            provider="imagine",
            operation="images_generations",
            model=self.model,
            elapsed_ms=round((time.monotonic() - started_at) * 1000, 3),
            response=_summarize_imagine_response_payload(response_payload) | {"image_count": len(image_paths)},
        )
        return ImagineResult(prompt=normalized_prompt, image_urls=image_urls, image_paths=image_paths)

    def _materialize_image_entries(self, entries: list[dict[str, str]]) -> tuple[list[str], list[Path]]:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        image_urls = []
        image_paths = []
        for entry in entries:
            if entry["url"]:
                image_urls.append(entry["url"])
                image_paths.append(self._download_single_image(entry["url"]))
            else:
                image_paths.append(self._write_image_bytes(_decode_base64_image(entry["b64_json"]), entry["mime_type"]))
        return image_urls, image_paths

    def _download_single_image(self, image_url: str) -> Path:
        try:
            response = requests.get(image_url, timeout=(10, 120))
            response.raise_for_status()
            return self._write_image_bytes(response.content, response.headers.get("Content-Type", ""), image_url=image_url)
        except requests.RequestException as exc:
            raise ImagineAPIError(f"下載圖片失敗: {exc}") from exc
        finally:
            if "response" in locals():
                response.close()

    def _write_image_bytes(self, image_bytes: bytes, content_type: str, image_url: str = "") -> Path:
        suffix = _resolve_image_suffix(image_url, content_type)
        target_path = self.download_dir / f"imagine_{uuid4().hex}{suffix}"
        target_path.write_bytes(image_bytes)
        return target_path


def _normalize_imagine_api_mode(api_mode: str | None) -> str:
    normalized = str(api_mode or "local").strip().lower()
    if normalized in {"", "local"}:
        return "local"
    if normalized == "gpt":
        return "gpt"
    raise ImagineAPIError("AI_IMAGINE_API_MODE 僅支援 local 或 gpt")


def _extract_generated_image_entries(payload: dict[str, Any]) -> list[dict[str, str]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("url") or "").strip()
        image_base64 = str(item.get("b64_json") or "").strip()
        mime_type = str(item.get("mime_type") or "").strip()
        if image_url or image_base64:
            entries.append({"url": image_url, "b64_json": image_base64, "mime_type": mime_type})
    return entries


def _load_response_json(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ImagineAPIError("Imagine API 返回無效 JSON") from exc
    if not isinstance(payload, dict):
        raise ImagineAPIError("Imagine API 返回無效 JSON")
    return payload


def _raise_imagine_http_error(response) -> None:
    status_code = int(getattr(response, "status_code", 200) or 200)
    if status_code < 400:
        return
    raise ImagineAPIError(f"Imagine API 請求失敗: {_extract_imagine_error_detail(response)}")


def _extract_imagine_error_detail(response) -> str:
    payload = None
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            error_type = str(error.get("type") or "").strip()
            if message and error_type:
                return f"{message} ({error_type})"
            if message:
                return message

    reason = str(getattr(response, "reason", "") or "").strip()
    status_code = int(getattr(response, "status_code", 0) or 0)
    if reason and status_code:
        return f"{status_code} {reason}"
    if reason:
        return reason
    if status_code:
        return str(status_code)
    return "未知錯誤"


def _decode_base64_image(encoded_image: str) -> bytes:
    try:
        return base64.b64decode(encoded_image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImagineAPIError("Imagine API 返回無效圖片資料") from exc


def _resolve_image_suffix(url: str, content_type: str) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip()
    if normalized:
        guessed = mimetypes.guess_extension(normalized)
        if guessed:
            return guessed
    parsed = urlparse(url)
    return Path(parsed.path).suffix.strip() or ".png"


def _summarize_imagine_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload": repr(payload)}
    summarized = dict(payload)
    data = summarized.get("data")
    if isinstance(data, list):
        summarized["data"] = [_summarize_image_entry(item) for item in data]
    return summarized


def _summarize_image_entry(item):
    if not isinstance(item, dict):
        return item
    entry = dict(item)
    if isinstance(entry.get("b64_json"), str):
        entry["b64_json"] = f"<base64 len={len(entry['b64_json'])}>"
    return entry


def _log_imagine_error(operation: str, model: str, started_at: float, exc: Exception, **fields) -> None:
    log_ai_api_event(
        "error",
        provider="imagine",
        operation=operation,
        model=model,
        elapsed_ms=round((time.monotonic() - started_at) * 1000, 3),
        error_type=type(exc).__name__,
        error=str(exc),
        **fields,
    )
