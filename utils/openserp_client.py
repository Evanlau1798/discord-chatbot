from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_OPENSERP_BASE_URL = "http://127.0.0.1:17000"
DEFAULT_OPENSERP_ENGINES = ("google", "bing", "duckduckgo", "ecosia")
DEFAULT_EXTRACTION_DEPTH = 5


@dataclass(frozen=True)
class OpenSerpSearchRequest:
    query: str
    language: str = "zh-TW"
    region: str = ""
    time_range: str = ""
    site_domains: tuple[str, ...] = ()
    desired_sources: int = 3


@dataclass(frozen=True)
class OpenSerpSource:
    title: str
    url: str
    snippet: str = ""
    text: str = ""
    content_format: str = ""
    domain: str = ""
    engines: tuple[str, ...] = ()
    rank: int = 0
    cluster_score: float = 0.0
    source_hint: str = ""
    source_category: str = ""


@dataclass(frozen=True)
class OpenSerpResponse:
    sources: tuple[OpenSerpSource, ...] = ()
    failed_engines: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    error: str = ""


class OpenSerpClient:
    def __init__(self, base_url: str, timeout_ms: int, engines: tuple[str, ...] = DEFAULT_OPENSERP_ENGINES):
        self.base_url = _normalize_base_url(base_url)
        self.timeout_ms = timeout_ms
        self.engines = tuple(engine for engine in engines if str(engine).strip()) or DEFAULT_OPENSERP_ENGINES

    def search(self, search: OpenSerpSearchRequest) -> OpenSerpResponse:
        try:
            payload = self._request(search)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return OpenSerpResponse(error=f"OpenSERP Search failed: {type(exc).__name__}")
        return _parse_response(payload)

    def _request(self, search: OpenSerpSearchRequest) -> dict:
        if not self.base_url:
            raise ValueError("missing OpenSERP base URL")
        parameters = {
            "text": str(search.query).strip(),
            "lang": search.language or "zh-TW",
            "engines": ",".join(self.engines),
            "mode": "balanced",
            "dedupe": "true",
            "merge": "true",
            "extract": str(DEFAULT_EXTRACTION_DEPTH),
            "extract_mode": "auto",
            "format": "json",
        }
        if search.region:
            parameters["region"] = search.region
        if search.time_range:
            parameters["date"] = search.time_range
        if search.site_domains:
            parameters["site"] = search.site_domains[0]
        request_url = f"{self.base_url}/mega/search?{urlencode(parameters)}"
        request = Request(request_url, headers={"Accept": "application/json", "User-Agent": "discord-chatbot/1.0"})
        with urlopen(request, timeout=max(1.0, self.timeout_ms / 1000)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid OpenSERP response")
        return payload


def _parse_response(payload: dict) -> OpenSerpResponse:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    failed = tuple(str(value) for value in meta.get("engines_failed", []) if str(value).strip())
    diagnostics = _engine_diagnostics(meta.get("engine_errors"))
    cluster_scores = _cluster_scores(payload.get("clusters"))
    sources = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        url = _clean_text(item.get("url"))
        if not url:
            continue
        extracted = item.get("extracted") if isinstance(item.get("extracted"), dict) else {}
        classification = item.get("classification") if isinstance(item.get("classification"), dict) else {}
        domain_info = item.get("domain_info") if isinstance(item.get("domain_info"), dict) else {}
        engine = _clean_text(item.get("engine"))
        sources.append(
            OpenSerpSource(
                title=_clean_text(item.get("title")),
                url=url,
                snippet=_clean_text(item.get("snippet")),
                text=_clean_text(extracted.get("content")) if not extracted.get("error") else "",
                content_format=_clean_text(extracted.get("format")),
                domain=_clean_text(item.get("domain")) or (urlparse(url).hostname or ""),
                engines=(engine,) if engine else (),
                rank=_safe_int(item.get("rank")),
                cluster_score=cluster_scores.get(_canonical_url(url), 0.0),
                source_hint=_clean_text(classification.get("source_hint")),
                source_category=_clean_text(domain_info.get("category")),
            )
        )
    return OpenSerpResponse(tuple(sources), failed, diagnostics)


def _cluster_scores(value) -> dict[str, float]:
    scores = {}
    for cluster in value if isinstance(value, list) else []:
        if not isinstance(cluster, dict):
            continue
        url = _canonical_url(cluster.get("canonical_url"))
        if url:
            try:
                scores[url] = float(cluster.get("score", 0.0))
            except (TypeError, ValueError):
                scores[url] = 0.0
    return scores


def _engine_diagnostics(value) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(f"{engine}:{reason}" for engine, reason in value.items() if str(engine).strip())


def _normalize_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if normalized and "://" not in normalized:
        normalized = f"http://{normalized}"
    return normalized


def _canonical_url(value) -> str:
    return str(value or "").strip().rstrip("/")


def _clean_text(value) -> str:
    return "\n".join(line.strip() for line in str(value or "").splitlines() if line.strip())


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
