from __future__ import annotations

import base64
import copy
import hashlib
import html
import logging
from dataclasses import dataclass
from enum import Enum

import requests

from utils.chat_client import ChatAPIError, ChatClientConfigError
from utils.openai_compatible_api import connection_error, http_error, validated_base_url

DEFAULT_NVIDIA_ASSET_BASE_URL = "https://api.nvcf.nvidia.com/v2/nvcf/assets"
DEFAULT_INLINE_MEDIA_MAX_BYTES = 180 * 1024
ASSET_REFERENCE_HEADER_MAX_CHARS = 370
ASSET_DESCRIPTION = "discord-chatbot-input"
ASSET_TIMEOUT = (10, 180)
logger = logging.getLogger("discord.utils.nvidia_assets")


class NvidiaAssetMode(str, Enum):
    INLINE = "inline"
    NVCF = "nvcf"


@dataclass(frozen=True)
class NvidiaAssetConfig:
    mode: NvidiaAssetMode = NvidiaAssetMode.INLINE
    inline_media_max_bytes: int = DEFAULT_INLINE_MEDIA_MAX_BYTES
    asset_base_url: str = DEFAULT_NVIDIA_ASSET_BASE_URL

    def __post_init__(self):
        try:
            mode = self.mode if isinstance(self.mode, NvidiaAssetMode) else NvidiaAssetMode(str(self.mode).strip().lower())
        except ValueError as exc:
            raise ChatClientConfigError("NVIDIA_ASSET_MODE 只支援 inline 或 nvcf") from exc
        if isinstance(self.inline_media_max_bytes, bool) or not isinstance(self.inline_media_max_bytes, int):
            raise ChatClientConfigError("NVIDIA_INLINE_MEDIA_MAX_BYTES 必須是正整數")
        if self.inline_media_max_bytes <= 0:
            raise ChatClientConfigError("NVIDIA_INLINE_MEDIA_MAX_BYTES 必須是正整數")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "asset_base_url", validated_base_url(self.asset_base_url))


@dataclass(frozen=True)
class PreparedNvidiaAssets:
    messages: list[dict]
    asset_ids: tuple[str, ...] = ()
    uploaded_bytes: int = 0


class NvidiaAssetManager:
    def __init__(self, config: NvidiaAssetConfig, api_key: str, *, session=None):
        self.config = config
        self.api_key = str(api_key or "").strip()
        self.session = session or requests

    def prepare(self, messages: list[dict]) -> PreparedNvidiaAssets:
        prepared = copy.deepcopy(messages)
        if self.config.mode is NvidiaAssetMode.INLINE:
            return PreparedNvidiaAssets(messages=prepared)
        created_ids: list[str] = []
        assets_by_hash: dict[str, str] = {}
        uploaded_bytes = 0
        try:
            for part in _image_parts(prepared):
                parsed = _parse_base64_data_url(part.get("image_url", {}).get("url"))
                if parsed is None:
                    continue
                mime_type, data = parsed
                if len(data) <= self.config.inline_media_max_bytes:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                asset_id = assets_by_hash.get(digest)
                if asset_id is None:
                    asset_id = self._create_and_upload(data, mime_type)
                    assets_by_hash[digest] = asset_id
                    created_ids.append(asset_id)
                    uploaded_bytes += len(data)
                part["image_url"]["url"] = f"data:{mime_type};asset_id,{asset_id}"
            _render_asset_messages(prepared)
            self.reference_header(tuple(created_ids))
            return PreparedNvidiaAssets(
                messages=prepared,
                asset_ids=tuple(created_ids),
                uploaded_bytes=uploaded_bytes,
            )
        except Exception:
            self.cleanup(tuple(created_ids))
            raise

    def reference_header(self, asset_ids: tuple[str, ...]) -> str:
        value = ",".join(asset_ids)
        if len(value) > ASSET_REFERENCE_HEADER_MAX_CHARS:
            raise ChatAPIError(
                "NVIDIA asset reference header 超過平台限制",
                provider="nvidia",
                status_code=413,
            )
        return value

    def cleanup(self, asset_ids: tuple[str, ...]) -> None:
        for asset_id in asset_ids:
            response = None
            try:
                response = self.session.delete(
                    f"{self.config.asset_base_url}/{asset_id}",
                    headers=self._auth_headers(),
                    timeout=ASSET_TIMEOUT,
                )
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code not in {200, 202, 204, 404}:
                    logger.warning(
                        "nvidia.asset_cleanup_failed status_code=%s error_type=http_error",
                        status_code,
                    )
            except Exception as exc:
                logger.warning("nvidia.asset_cleanup_failed error_type=%s", type(exc).__name__)
            finally:
                if response is not None:
                    response.close()

    def _create_and_upload(self, data: bytes, mime_type: str) -> str:
        response = None
        asset_id = ""
        try:
            response = self.session.post(
                self.config.asset_base_url,
                headers={**self._auth_headers(), "Content-Type": "application/json"},
                json={"contentType": mime_type, "description": ASSET_DESCRIPTION},
                timeout=ASSET_TIMEOUT,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code != 200:
                raise http_error("nvidia", status_code)
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise ChatAPIError("NVIDIA asset API 回應不是有效 JSON", provider="nvidia") from exc
            if not isinstance(payload, dict):
                raise ChatAPIError("NVIDIA asset API 回應格式錯誤", provider="nvidia")
            asset_id = str(payload.get("assetId") or "").strip()
            upload_url = str(payload.get("uploadUrl") or "").strip()
            if not asset_id or not upload_url:
                raise ChatAPIError("NVIDIA asset API 缺少 assetId 或 uploadUrl", provider="nvidia")
        except requests.RequestException as exc:
            raise connection_error("nvidia") from exc
        except Exception:
            if asset_id:
                self.cleanup((asset_id,))
            raise
        finally:
            if response is not None:
                response.close()
        try:
            self._upload(upload_url, data, mime_type)
        except Exception:
            self.cleanup((asset_id,))
            raise
        return asset_id

    def _upload(self, upload_url: str, data: bytes, mime_type: str) -> None:
        response = None
        try:
            response = self.session.put(
                upload_url,
                headers={
                    "Content-Type": mime_type,
                    "x-amz-meta-nvcf-asset-description": ASSET_DESCRIPTION,
                },
                data=data,
                timeout=ASSET_TIMEOUT,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code not in {200, 201, 204}:
                raise http_error("nvidia", status_code)
        except requests.RequestException as exc:
            raise connection_error("nvidia") from exc
        finally:
            if response is not None:
                response.close()

    def _auth_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}


def _image_parts(messages: list[dict]):
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                yield part


def _parse_base64_data_url(value) -> tuple[str, bytes] | None:
    text = str(value or "")
    if not text.startswith("data:") or ";base64," not in text:
        return None
    metadata, encoded = text[5:].split(";base64,", 1)
    mime_type = metadata.split(";", 1)[0].strip()
    if not mime_type.startswith("image/"):
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    return (mime_type, data) if data else None


def _render_asset_messages(messages: list[dict]) -> None:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list) or not _contains_asset_reference(content):
            continue
        chunks = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    chunks.append(text)
                continue
            if part.get("type") == "image_url":
                url = str(part.get("image_url", {}).get("url") or "").strip()
                if url:
                    chunks.append(f'<img src="{html.escape(url, quote=True)}" />')
        message["content"] = "\n\n".join(chunks)


def _contains_asset_reference(content: list) -> bool:
    return any(
        isinstance(part, dict)
        and part.get("type") == "image_url"
        and ";asset_id," in str(part.get("image_url", {}).get("url") or "")
        for part in content
    )
