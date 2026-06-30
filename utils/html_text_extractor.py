from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from utils.message_media import sanitize_image_urls


@dataclass(frozen=True)
class HTMLTextExtraction:
    title: str
    text: str
    image_urls: tuple[str, ...] = ()


BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
SKIP_TAGS = {"canvas", "noscript", "script", "style", "svg", "template"}
BODY_CHROME_TAGS = {"aside", "footer", "form", "nav"}


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._body_depth = 0
        self._main_depth = 0
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._main_parts: list[str] = []
        self._image_refs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attr_map = {str(key).lower(): str(value or "").strip() for key, value in attrs}
        if tag == "body":
            self._body_depth += 1
        if tag == "main" and self._body_depth:
            self._main_depth += 1
        if tag == "title":
            self._in_title = True
        self._collect_image_refs(tag, attr_map)
        if tag in SKIP_TAGS or (self._body_depth and not self._main_depth and tag in BODY_CHROME_TAGS):
            self._skip_depth += 1
        if self._body_depth and tag in BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._body_depth and tag in BLOCK_TAGS:
            self._append_break()
        if (tag in SKIP_TAGS or tag in BODY_CHROME_TAGS) and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "body" and self._body_depth:
            self._body_depth -= 1
        if tag == "main" and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._body_depth:
            self._text_parts.append(data)
        if self._main_depth:
            self._main_parts.append(data)

    def extraction(self, base_url: str = "") -> HTMLTextExtraction:
        main_text = _normalize_text(" ".join(self._main_parts))
        body_text = _normalize_text(" ".join(self._text_parts))
        return HTMLTextExtraction(
            title=_normalize_text(" ".join(self._title_parts)),
            text=main_text or body_text,
            image_urls=tuple(sanitize_image_urls(self._image_refs, limit=10, base_url=base_url)),
        )

    def _append_break(self) -> None:
        self._text_parts.append("\n")
        if self._main_depth:
            self._main_parts.append("\n")

    def _collect_image_refs(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "meta" and attrs.get("content"):
            key = attrs.get("property") or attrs.get("name")
            if key and key.lower() in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._image_refs.append(attrs["content"])
        if tag == "img":
            for key in ("src", "data-src", "data-original"):
                if attrs.get(key):
                    self._image_refs.append(attrs[key])
            self._image_refs.extend(_parse_srcset(attrs.get("srcset", "")))
        if tag == "source" and attrs.get("srcset"):
            self._image_refs.extend(_parse_srcset(attrs["srcset"]))


def extract_html_text(html: str, base_url: str = "") -> HTMLTextExtraction:
    parser = ReadableHTMLParser()
    parser.feed(str(html or ""))
    parser.close()
    return parser.extraction(base_url=base_url)


def _normalize_text(text: str) -> str:
    lines = []
    for line in str(text or "").replace("\r", "\n").splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _parse_srcset(srcset: str) -> list[str]:
    refs = []
    for candidate in str(srcset or "").split(","):
        ref = candidate.strip().split(" ", 1)[0].strip()
        if ref:
            refs.append(ref)
    return refs
