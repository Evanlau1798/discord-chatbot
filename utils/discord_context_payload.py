from __future__ import annotations

from utils.discord_media_attachments import attachment_duration_seconds, message_is_voice_message


def build_attachment_payload(message) -> list[dict]:
    attachments = []
    is_voice_message = message_is_voice_message(message)
    for attachment in getattr(message, "attachments", []) or []:
        payload = {
            "filename": getattr(attachment, "filename", ""),
            "url": getattr(attachment, "url", ""),
            "contentType": getattr(attachment, "content_type", ""),
        }
        duration = attachment_duration_seconds(attachment)
        if duration is not None:
            payload["durationSeconds"] = duration
        if is_voice_message:
            payload["isVoiceMessage"] = True
        attachments.append(payload)
    return attachments


def build_embed_payload(message) -> list[dict]:
    embeds = []
    for embed in getattr(message, "embeds", []) or []:
        payload = {}
        for source_key, payload_key in (("url", "url"), ("title", "title"), ("description", "description")):
            value = str(_embed_value(embed, source_key) or "").strip()
            if value:
                payload[payload_key] = value[:500]
        for source_key, payload_key in (("image", "imageUrl"), ("thumbnail", "thumbnailUrl"), ("video", "videoUrl")):
            url = _embed_proxy_url(_embed_value(embed, source_key))
            if url:
                payload[payload_key] = url
        if payload:
            embeds.append(payload)
    return embeds[:5]


def _embed_value(value, key: str):
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _embed_proxy_url(proxy) -> str:
    if proxy is None:
        return ""
    url = _embed_value(proxy, "url") or _embed_value(proxy, "proxy_url")
    return str(url or "").strip()
