from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

from utils.browser_result_types import BrowserToolError

MAX_URL_LENGTH = 2000


@dataclass(frozen=True)
class BrowserTarget:
    source_type: str
    url: str
    query: str = ""

    def __getitem__(self, key: str) -> str:
        if key == "source_type":
            return self.source_type
        if key == "url":
            return self.url
        if key == "query":
            return self.query
        raise KeyError(key)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def normalize_url(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        raise BrowserToolError("browser url 不可為空")
    if len(normalized) > MAX_URL_LENGTH:
        raise BrowserToolError("browser url 過長")
    if "://" not in normalized:
        normalized = f"https://{normalized}"
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrowserToolError(f"browser url 格式不合法: {url}")
    if parsed.username or parsed.password:
        raise BrowserToolError("browser url 不可包含帳號或密碼")
    if _is_private_or_local_host(parsed.hostname or ""):
        raise BrowserToolError("browser url 不可指向 localhost 或私有網路位址")
    return normalized


def normalize_search_query(query: str) -> str:
    normalized = str(query or "").strip()
    if not normalized:
        raise BrowserToolError("browser searchQuery 不可為空")
    return normalized


def normalize_search_queries(queries: list[str]) -> list[str]:
    normalized_queries = []
    for query in queries:
        if not str(query or "").strip():
            continue
        normalized = normalize_search_query(query)
        if normalized not in normalized_queries:
            normalized_queries.append(normalized)
    return normalized_queries


def build_url_target(url: str) -> BrowserTarget:
    return BrowserTarget(source_type="url", query="", url=normalize_url(url))


def dedupe_targets(targets: list) -> list:
    deduped = []
    seen_urls = set()
    for target in targets:
        _append_target_once(deduped, seen_urls, target)
    return deduped


def _append_target_once(targets: list, seen_urls: set[str], target) -> None:
    url = _target_url(target)
    if url in seen_urls:
        return
    seen_urls.add(url)
    targets.append(target)


def _target_url(target) -> str:
    return target.url if isinstance(target, BrowserTarget) else target["url"]


def _is_private_or_local_host(hostname: str) -> bool:
    normalized = str(hostname or "").strip().lower().strip("[]")
    if not normalized:
        return True
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".localhost"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return any((
        address.is_loopback,
        address.is_private,
        address.is_link_local,
        address.is_reserved,
        address.is_multicast,
        address.is_unspecified,
    ))
