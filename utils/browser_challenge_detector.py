from __future__ import annotations

import asyncio
from dataclasses import dataclass


EMPTY_PAGE_ERROR = "頁面沒有可讀文字，無法可靠提供給模型。"
ANTI_BOT_ERROR = "頁面顯示 CAPTCHA 或反機器人驗證，無法可靠讀取內容。"
UNRELIABLE_PAGE_ERROR = "頁面顯示錯誤或暫時無法提供可靠內容。"

ANTI_BOT_TEXT_MARKERS = (
    "access denied",
    "attention required",
    "are you a robot",
    "captcha",
    "cf-turnstile",
    "checking if the site connection is secure",
    "checking your browser",
    "cloudflare",
    "enable javascript and cookies",
    "geetest",
    "hcaptcha",
    "human verification",
    "just a moment",
    "mtcaptcha",
    "please complete the following challenge",
    "our systems have detected unusual traffic",
    "please enable javascript",
    "please enable cookies",
    "recaptcha",
    "request blocked",
    "select all squares containing a duck",
    "solve the following challenge to continue",
    "sorry, you have been blocked",
    "turnstile",
    "unfortunately, bots use duckduckgo too",
    "verify you are human",
    "unusual traffic",
    "不是由自動程式發出",
    "流量有異常",
    "請啟用 javascript",
    "請確認你不是機器人",
    "請解決以下挑戰以繼續",
    "驗證您是真人",
    "機器人驗證",
)
UNRELIABLE_PAGE_MARKERS = (
    "404 not found",
    "the requested url was not found",
    "webpage is not available",
    "網頁已被移除",
    "網頁搜尋遇到暫時性的問題",
)
CAPTCHA_CONTEXT_MARKERS = (
    "challenge",
    "human",
    "robot",
    "security check",
    "verification",
    "verify",
    "安全檢查",
    "真人",
    "機器人",
    "驗證",
)
CAPTCHA_DOM_SELECTORS = (
    ".cf-turnstile",
    ".g-recaptcha",
    ".geetest_canvas_slice",
    ".geetest_radar_tip",
    ".geetest_slider_button",
    ".geetest_window",
    ".h-captcha",
    ".mtcaptcha",
    ".rc-imageselect-instructions",
    ".recaptcha-checkbox-border",
    "#mtcaptcha-iframe-1",
    "#recaptcha-verify-button",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='mtcaptcha']",
    "iframe[src*='recaptcha']",
)
CAPTCHA_FRAME_MARKERS = (
    "captcha",
    "challenge",
    "challenges.cloudflare.com",
    "geetest",
    "hcaptcha",
    "mtcaptcha",
    "recaptcha",
    "turnstile",
)

_CAPTCHA_DOM_SCRIPT = """
({ selectors, frameMarkers }) => {
  const isVisible = (element) => {
    if (!element || !element.getBoundingClientRect) {
      return false;
    }
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0
      && rect.height > 0;
  };

  for (const selector of selectors) {
    try {
      for (const element of document.querySelectorAll(selector)) {
        if (isVisible(element)) {
          return true;
        }
      }
    } catch (_) {
    }
  }

  for (const frame of document.querySelectorAll('iframe')) {
    const frameText = [
      frame.title || '',
      frame.name || '',
      frame.id || '',
      frame.className || '',
      frame.src || '',
    ].join(' ').toLowerCase();
    if (isVisible(frame) && frameMarkers.some((marker) => frameText.includes(marker))) {
      return true;
    }
  }

  return false;
}
"""


@dataclass(frozen=True)
class ReliablePageContent:
    text: str
    error: str = ""


async def detect_captcha_challenge(page) -> bool:
    return await _safe_page_evaluate(
        page,
        _CAPTCHA_DOM_SCRIPT,
        {
            "selectors": CAPTCHA_DOM_SELECTORS,
            "frameMarkers": CAPTCHA_FRAME_MARKERS,
        },
    )


def build_reliable_content(title: str, text: str, *, has_captcha_challenge: bool = False) -> ReliablePageContent:
    normalized_text = normalize_text(text)
    challenge_error = _detect_anti_bot_challenge(title, normalized_text, has_captcha_challenge)
    if challenge_error:
        return ReliablePageContent(text="", error=challenge_error)
    unreliable_error = _detect_unreliable_page(title, normalized_text)
    if unreliable_error:
        return ReliablePageContent(text="", error=unreliable_error)
    if not normalized_text:
        return ReliablePageContent(text="", error=EMPTY_PAGE_ERROR)
    return ReliablePageContent(text=normalized_text)


def normalize_text(text: str) -> str:
    return "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())


async def _safe_page_evaluate(page, script: str, argument: dict) -> bool:
    try:
        value = page.evaluate(script, argument)
        if asyncio.iscoroutine(value):
            value = await value
        return bool(value)
    except Exception:
        return False


def _detect_anti_bot_challenge(title: str, text: str, has_captcha_challenge: bool) -> str:
    haystack = f"{title}\n{text[:3000]}".lower()
    if any(marker in haystack for marker in ANTI_BOT_TEXT_MARKERS):
        return ANTI_BOT_ERROR
    if has_captcha_challenge and _looks_like_blocking_challenge(title, text):
        return ANTI_BOT_ERROR
    return ""


def _looks_like_blocking_challenge(title: str, text: str) -> bool:
    if not text:
        return True
    haystack = f"{title}\n{text[:1200]}".lower()
    return any(marker in haystack for marker in CAPTCHA_CONTEXT_MARKERS)


def _detect_unreliable_page(title: str, text: str) -> str:
    haystack = f"{title}\n{text[:3000]}".lower()
    if any(marker in haystack for marker in UNRELIABLE_PAGE_MARKERS):
        return UNRELIABLE_PAGE_ERROR
    return ""
