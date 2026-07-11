from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from utils.browser_actions import normalize_search_queries
from utils.browser_result_types import BrowserFetchResult
from utils.openserp_client import DEFAULT_OPENSERP_BASE_URL, OpenSerpClient, OpenSerpSearchRequest, OpenSerpSource

OPENSERP_BASE_URL_ENV = "OPENSERP_BASE_URL"
OPENSERP_MAX_QUERIES_ENV = "OPENSERP_MAX_QUERIES_PER_TURN"
OPENSERP_LANGUAGE_ENV = "OPENSERP_LANGUAGE"
OPENSERP_REGION_ENV = "OPENSERP_REGION"
OPENSERP_TIME_RANGE_ENV = "OPENSERP_TIME_RANGE"
OPENSERP_DESIRED_SOURCES_ENV = "OPENSERP_DESIRED_SOURCES"
DEFAULT_MAX_QUERIES = 3
DEFAULT_DESIRED_SOURCES = 3
DEFAULT_PER_SOURCE_CHARS = 12_000
DEFAULT_TOTAL_CHARS = 36_000
MIN_RELIABLE_SOURCES = 2
_GLOBAL_SEARCH_LIMITER = threading.BoundedSemaphore(3)
_TRACKING_PARAMETERS = {"fbclid", "gclid", "msclkid", "ref", "ref_src"}
_UNSAFE_TLDS = {"adult", "porn", "sex", "sexy", "xxx"}
_UNSAFE_HOST_TOKENS = {"hentai", "hqtube", "porn", "porno", "redtube", "xnxx", "xvideos", "xxx"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchOptions:
    language: str = "zh-TW"
    region: str = ""
    time_range: str = ""
    site_domains: tuple[str, ...] = ()
    desired_sources: int = DEFAULT_DESIRED_SOURCES
    source_profile: str = "mixed"


class SearchPlanner:
    def __init__(self, timeout_ms: int, *, client: OpenSerpClient | None = None):
        self.timeout_ms = timeout_ms
        base_url = os.getenv(OPENSERP_BASE_URL_ENV, DEFAULT_OPENSERP_BASE_URL).strip() or DEFAULT_OPENSERP_BASE_URL
        self.client = client or OpenSerpClient(base_url, timeout_ms)
        self.max_queries = _env_int(OPENSERP_MAX_QUERIES_ENV, DEFAULT_MAX_QUERIES, 1, 3)
        self.language = os.getenv(OPENSERP_LANGUAGE_ENV, "zh-TW").strip() or "zh-TW"
        self.region = os.getenv(OPENSERP_REGION_ENV, "").strip()
        self.time_range = os.getenv(OPENSERP_TIME_RANGE_ENV, "").strip()
        self.desired_sources = _env_int(OPENSERP_DESIRED_SOURCES_ENV, DEFAULT_DESIRED_SOURCES, 3, 5)

    async def search_many(self, queries: list[str], *, options: SearchOptions | None = None) -> list[BrowserFetchResult]:
        started_at = time.monotonic()
        planned = self.plan_queries(queries)
        if not planned:
            return []
        resolved = options or SearchOptions(
            language=self.language,
            region=self.region,
            time_range=self.time_range,
            desired_sources=self.desired_sources,
        )
        responses = await asyncio.gather(*(asyncio.to_thread(self._search_limited, query, resolved) for query in planned))
        candidates = [
            (query, source)
            for query, response in zip(planned, responses)
            for source in response.sources
        ]
        selected = select_reliable_sources(
            candidates,
            desired_sources=resolved.desired_sources,
            site_domains=resolved.site_domains,
            source_profile=resolved.source_profile,
        )
        failed_engines = sorted({engine for response in responses for engine in response.failed_engines})
        diagnostics = tuple(item for response in responses for item in response.diagnostics)
        source_chars = sum(len(source.text) for _query, source in candidates)
        selected_chars = sum(len(source.text) for source in selected)
        logger.info(
            "openserp.search_complete queries=%s candidates=%s selected=%s failed_engines=%s "
            "captcha=%s elapsed_ms=%s truncated_chars=%s",
            len(planned),
            len(candidates),
            len(selected),
            ",".join(failed_engines) or "none",
            any("captcha" in item.lower() for item in diagnostics),
            round((time.monotonic() - started_at) * 1000),
            max(0, source_chars - selected_chars),
        )
        if _distinct_source_count(selected) < MIN_RELIABLE_SOURCES and not _has_first_party_source(selected, resolved.site_domains):
            errors = tuple(response.error for response in responses if response.error)
            return [_unreliable_result(planned, diagnostics, errors)]
        return [_browser_result(source, planned) for source in selected]

    def plan_queries(self, queries: list[str]) -> list[str]:
        return normalize_search_queries(queries)[: self.max_queries]

    def _search_limited(self, query: str, options: SearchOptions):
        with _GLOBAL_SEARCH_LIMITER:
            return self.client.search(
                OpenSerpSearchRequest(
                    query=query,
                    language=options.language,
                    region=options.region,
                    time_range=options.time_range,
                    site_domains=options.site_domains,
                    desired_sources=options.desired_sources,
                )
            )


def select_reliable_sources(
    candidates: list[tuple[str, OpenSerpSource]],
    *,
    desired_sources: int,
    site_domains: tuple[str, ...] = (),
    source_profile: str = "mixed",
    per_source_chars: int = DEFAULT_PER_SOURCE_CHARS,
    total_chars: int = DEFAULT_TOTAL_CHARS,
) -> list[OpenSerpSource]:
    merged: dict[str, tuple[str, OpenSerpSource]] = {}
    for query, source in candidates:
        canonical = canonicalize_source_url(source.url)
        if not canonical or not source.text.strip() or _is_unsafe_source(canonical) or _is_query_conflict(query, canonical):
            continue
        normalized = replace(source, url=canonical)
        if _relevance_score(query, normalized) == 0:
            continue
        current = merged.get(canonical)
        if current is None or _source_score(query, normalized, site_domains, source_profile) > _source_score(current[0], current[1], site_domains, source_profile):
            merged[canonical] = (query, normalized)
    ranked = sorted(
        merged.values(),
        key=lambda item: _source_score(item[0], item[1], site_domains, source_profile),
        reverse=True,
    )
    ranked = _diversify_domains(ranked)
    selected = []
    remaining = max(0, int(total_chars))
    for _query, source in ranked[: max(1, min(5, int(desired_sources)))]:
        if remaining <= 0:
            break
        limit = min(max(1, int(per_source_chars)), remaining)
        clipped = source.text[:limit]
        if not clipped:
            continue
        selected.append(replace(source, text=clipped))
        remaining -= len(clipped)
    return selected


def canonicalize_source_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    port = parsed.port
    netloc = hostname if port in {None, 80 if scheme == "http" else 443} else f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or ""
    query = urlencode(sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
    ))
    return urlunsplit((scheme, netloc, path, query, ""))


def plan_search_queries_from_env(queries: list[str]) -> list[str]:
    maximum = _env_int(OPENSERP_MAX_QUERIES_ENV, DEFAULT_MAX_QUERIES, 1, 3)
    return normalize_search_queries(queries)[:maximum]


def _source_score(
    query: str,
    source: OpenSerpSource,
    site_domains: tuple[str, ...],
    source_profile: str,
) -> tuple:
    hostname = (urlsplit(source.url).hostname or "").lower()
    official = any(hostname == domain.lower() or hostname.endswith(f".{domain.lower()}") for domain in site_domains)
    relevance = _relevance_score(query, source)
    signals = _source_signals(source)
    authority = bool(signals & {"gov", "edu", "mil", "academic", "document"})
    rank = source.rank if source.rank > 0 else 10_000
    return official, _profile_score(signals, source_profile), authority, source.cluster_score, relevance, -rank


def _source_signals(source: OpenSerpSource) -> set[str]:
    parsed = urlsplit(source.url)
    hostname = (parsed.hostname or source.domain or "").lower().removeprefix("www.")
    title = source.title.lower()
    signals = {
        str(source.source_hint or "").strip().lower().replace("-", "_"),
        str(source.source_category or "").strip().lower().replace("-", "_"),
    }
    if hostname.startswith(("docs.", "developer.")) or "/docs" in parsed.path.lower() or "documentation" in title:
        signals.add("document")
    if hostname in {"github.com", "gitlab.com"}:
        signals.add("code_repository")
    if hostname in {"stackoverflow.com", "stackexchange.com"} or hostname.endswith(".stackexchange.com"):
        signals.add("qa_forum")
    if ".gov." in hostname or hostname.endswith(".gov"):
        signals.add("gov")
    signals.discard("")
    return signals


def _profile_score(signals: set[str], source_profile: str) -> int:
    preferred = {
        "official": {"gov", "edu", "mil", "academic", "document"},
        "news": {"news"},
        "technical": {"academic", "document", "code_repository", "qa_forum", "edu"},
        "reviews": {"forum", "social", "marketplace", "social_forum", "social_media", "qa_forum"},
        "local": {"forum", "social", "marketplace", "social_forum", "social_media"},
        "mixed": {
            "gov", "edu", "mil", "academic", "document", "news",
        },
    }
    return int(bool(signals & preferred.get(source_profile, preferred["mixed"])))


def _diversify_domains(ranked: list[tuple[str, OpenSerpSource]]) -> list[tuple[str, OpenSerpSource]]:
    unique = []
    repeated = []
    seen = set()
    for item in ranked:
        domain = _source_domain(item[1])
        target = repeated if domain in seen else unique
        target.append(item)
        seen.add(domain)
    return [*unique, *repeated]


def _distinct_source_count(sources: list[OpenSerpSource]) -> int:
    return len({_source_domain(source) for source in sources if _source_domain(source)})


def _source_domain(source: OpenSerpSource) -> str:
    hostname = (urlsplit(source.url).hostname or source.domain or "").lower()
    return hostname.removeprefix("www.")


def _relevance_score(query: str, source: OpenSerpSource) -> int:
    raw_terms = re.findall(r"[\w-]+", query.lower())
    query_terms = {
        term
        for term in raw_terms
        if len(term) >= 3 or (len(term) >= 2 and any("\u4e00" <= char <= "\u9fff" for char in term))
    }
    if not query_terms:
        return 1
    haystack = f"{source.title} {source.snippet} {source.domain}".lower()
    haystack_terms = set(re.findall(r"[a-z0-9_-]+", haystack))
    score = 0
    for term in query_terms:
        if any("\u4e00" <= char <= "\u9fff" for char in term):
            score += int(term in haystack)
        else:
            score += int(term in haystack_terms)
    return score


def _has_first_party_source(sources: list[OpenSerpSource], site_domains: tuple[str, ...]) -> bool:
    if not site_domains or len(sources) != 1:
        return False
    hostname = (urlsplit(sources[0].url).hostname or "").lower()
    return any(hostname == domain.lower() or hostname.endswith(f".{domain.lower()}") for domain in site_domains)


def _is_unsafe_source(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    labels = [label for label in hostname.split(".") if label]
    if labels and labels[-1] in _UNSAFE_TLDS:
        return True
    tokens = {token for label in labels for token in re.split(r"[-_]", label) if token}
    return bool(tokens & _UNSAFE_HOST_TOKENS)


def _is_query_conflict(query: str, url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return "-self" in hostname and "self" not in query.lower().split()


def _browser_result(source: OpenSerpSource, queries: list[str]) -> BrowserFetchResult:
    engine_text = ",".join(source.engines) or "unknown"
    return BrowserFetchResult(
        requested_url=source.url,
        source_type="search",
        query=" | ".join(queries),
        final_url=source.url,
        title=source.title,
        text=source.text,
        content_format=source.content_format or "markdown",
        total_chars=len(source.text),
        diagnostics=("openserp", f"engines:{engine_text}", f"cluster_score:{source.cluster_score:g}"),
    )


def _unreliable_result(queries: list[str], diagnostics: tuple[str, ...], errors: tuple[str, ...]) -> BrowserFetchResult:
    detail = errors[0] if errors else "OpenSERP 可讀來源少於 2 個。"
    return BrowserFetchResult(
        requested_url=" | ".join(queries),
        source_type="search",
        query=" | ".join(queries),
        title="OpenSERP Search",
        error=f"搜尋可靠來源不足: {detail}",
        diagnostics=("openserp_insufficient_sources", *diagnostics[:8]),
    )


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)
