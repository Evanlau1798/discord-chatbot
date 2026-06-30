from __future__ import annotations

SPARSE_PAGE_ERROR = "頁面只取得導覽、選單或過少內容，無法可靠提供給模型。"
HTTP_MIN_TEXT_CHARS = 240
FINAL_MIN_TEXT_CHARS = 80
_LEADING_SCAN_LIMIT = 80

_LOW_VALUE_EXACT_LINES = {
    "|",
    "...",
    ":::",
    "下載",
    "搜尋",
    "確定",
    "關閉",
    "登入",
    "登出",
    "上一頁",
    "下一頁",
    "回首頁",
    "網站導覽",
    "開始搜尋",
    "請輸入關鍵字",
    "選擇縣市",
    "選擇鄉鎮",
    "快速地點搜尋",
    "會員資料",
    "隱私權政策",
    "服務條款",
    "Theme",
    "Auto",
    "Light",
    "Dark",
}
_LOW_VALUE_MARKERS = (
    "cookie",
    "cookies",
    "privacy policy",
    "隱私聲明",
    "使用相關技術提供更好的閱讀體驗",
    "your browser does not appear",
    "跳到主要內容",
    "協助工具",
    "意見反應",
    "字級",
    "點此將",
    "社群分享",
)


def prepare_browser_text(text: str) -> str:
    lines = _clean_lines(text)
    if not lines:
        return ""
    content_lines = lines[_content_start_index(lines):]
    compacted = [line for line in content_lines if not _is_low_value_line(line)]
    return "\n".join(compacted or content_lines)


def is_useful_http_text(text: str) -> bool:
    prepared = prepare_browser_text(text)
    return _has_useful_text(prepared, min_chars=HTTP_MIN_TEXT_CHARS, min_informative_lines=2)


def is_useful_final_text(text: str) -> bool:
    prepared = prepare_browser_text(text)
    return _has_useful_text(prepared, min_chars=FINAL_MIN_TEXT_CHARS, min_informative_lines=1)


def _has_useful_text(text: str, *, min_chars: int, min_informative_lines: int) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if len(normalized) >= min_chars:
        return True
    informative_lines = [line for line in _clean_lines(normalized) if _is_informative_line(line)]
    return len(informative_lines) >= min_informative_lines


def _content_start_index(lines: list[str]) -> int:
    scan_limit = min(len(lines), _LEADING_SCAN_LIMIT)
    for index in range(scan_limit):
        if _is_informative_line(lines[index]) and _nearby_has_signal(lines, index):
            return index
    return 0


def _nearby_has_signal(lines: list[str], index: int) -> bool:
    window = lines[index:index + 8]
    return sum(1 for line in window if _is_informative_line(line)) >= 2


def _is_informative_line(line: str) -> bool:
    normalized = line.strip()
    if _is_low_value_line(normalized):
        return False
    if len(normalized) >= 18:
        return True
    return _has_alpha_or_cjk(normalized) and any(char.isdigit() for char in normalized)


def _is_low_value_line(line: str) -> bool:
    normalized = line.strip()
    if not normalized:
        return True
    if normalized in _LOW_VALUE_EXACT_LINES:
        return True
    lowered = normalized.lower()
    if any(marker in lowered for marker in _LOW_VALUE_MARKERS):
        return True
    if len(normalized) <= 2 and not any(char.isdigit() for char in normalized):
        return True
    return False


def _has_alpha_or_cjk(value: str) -> bool:
    for char in value:
        if char.isalpha() or "\u4e00" <= char <= "\u9fff":
            return True
    return False


def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]
