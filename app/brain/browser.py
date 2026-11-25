from __future__ import annotations

import logging
import re
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("nalla.browser")

_CONSEQUENTIAL_WORDS = {
    "submit",
    "buy",
    "purchase",
    "delete",
    "remove",
    "pay",
    "checkout",
    "order",
    "confirm",
    "transfer",
    "terminate",
}


def is_consequential_target(target: str) -> bool:
    """Return True if clicking or typing into target is a consequential action."""
    words = set(re.findall(r"\b\w+\b", target.lower()))
    return bool(words & _CONSEQUENTIAL_WORDS)


def _focus_window_with_title(title_snippet: str, timeout_seconds: float = 2.0) -> bool:
    """Bring the window matching title_snippet to foreground on Windows."""
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.EnumWindows.argtypes = [enum_windows_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT
    ]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def find_window() -> int | None:
        matches: list[int] = []

        @enum_windows_proc
        def collect_window(hwnd: int, _: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title_snippet.lower() in buffer.value.lower():
                matches.append(hwnd)
                return False
            return True

        user32.EnumWindows(collect_window, 0)
        return matches[0] if matches else None

    deadline = time.monotonic() + timeout_seconds
    hwnd = find_window()
    while hwnd is None and time.monotonic() < deadline:
        time.sleep(0.05)
        hwnd = find_window()
    if hwnd is None:
        return False

    foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    attached = bool(
        foreground_thread
        and foreground_thread != current_thread
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_NOTOPMOST, SWP_SHOWWINDOW
        user32.SetForegroundWindow(hwnd)
        return True
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)


class BrowserManager:
    """Manages an isolated Playwright Chromium browser instance on a dedicated worker thread."""

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._lock = threading.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._thread: threading.Thread | None = None
        self._work_queue: Any = None

    @staticmethod
    def validate_url(url: str) -> str:
        """Validate that a URL is well-formed, http/https, and contains no credentials."""
        candidate = (url or "").strip()
        if not candidate:
            raise ValueError("URL cannot be empty.")
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(f"Unsupported scheme '{parsed.scheme}'. Only http and https are allowed.")
        if not parsed.netloc:
            raise ValueError(f"Invalid URL '{candidate}': missing domain/hostname.")
        if "@" in parsed.netloc:
            raise ValueError("URLs with embedded user credentials are not allowed.")
        return candidate

    def _ensure_browser(self) -> None:
        """Lazily initialize Playwright Chromium."""
        if self._page is not None and not self._page.is_closed():
            return

        from playwright.sync_api import sync_playwright
        if self._playwright is None:
            self._playwright = sync_playwright().start()

        if self._browser is None:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )

        if self._context is None:
            self._context = self._browser.new_context(no_viewport=True)

        self._page = self._context.new_page()
        self._page.set_default_timeout(15000)

    def _bring_to_front(self) -> None:
        """Bring the browser window to the foreground if not headless."""
        if self.headless or sys.platform != "win32":
            return
        try:
            if self._page:
                title = self._page.title() or "Chromium"
                _focus_window_with_title(title, timeout_seconds=1.0)
        except Exception as exc:
            logger.debug("Failed to bring window to front: %s", exc)

    def open_url(self, url: str) -> dict[str, Any]:
        """Open a URL and return title and final URL."""
        validated = self.validate_url(url)
        with self._lock:
            self._ensure_browser()
            self._page.goto(validated, wait_until="domcontentloaded")
            self._bring_to_front()
            return {
                "title": self._page.title(),
                "url": self._page.url,
            }

    def search_the_web(self, query: str) -> dict[str, Any]:
        """Search DuckDuckGo in the browser."""
        from urllib.parse import urlencode
        q = (query or "").strip()
        if not q:
            raise ValueError("Search query cannot be empty.")
        search_url = f"https://duckduckgo.com/?{urlencode({'q': q})}"
        return self.open_url(search_url)

    def read_page(self, max_chars: int = 2500) -> dict[str, Any]:
        """Read normalized visible text from the active page."""
        with self._lock:
            if not self._page:
                raise RuntimeError("No browser page is currently open. Call open_url first.")
            # Extract readable body text
            text = self._page.evaluate(
                """() => {
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        {
                            acceptNode: (node) => {
                                const parent = node.parentElement;
                                if (!parent) return NodeFilter.FILTER_REJECT;
                                const tag = parent.tagName.toLowerCase();
                                if (['script', 'style', 'noscript', 'svg'].includes(tag)) {
                                    return NodeFilter.FILTER_REJECT;
                                }
                                const style = window.getComputedStyle(parent);
                                if (style.display === 'none' || style.visibility === 'hidden') {
                                    return NodeFilter.FILTER_REJECT;
                                }
                                return NodeFilter.FILTER_ACCEPT;
                            }
                        }
                    );
                    const pieces = [];
                    while (walker.nextNode()) {
                        const val = walker.currentNode.nodeValue.trim();
                        if (val) pieces.push(val);
                    }
                    return pieces.join(' ');
                }"""
            )
            normalized = re.sub(r"\s+", " ", text or "").strip()
            truncated = normalized[:max_chars]
            if len(normalized) > max_chars:
                truncated += "... [truncated]"
            return {
                "title": self._page.title(),
                "url": self._page.url,
                "text": truncated,
            }

    def inspect_page(self, max_elements: int = 40) -> dict[str, Any]:
        """Inspect visible interactive elements on the page."""
        with self._lock:
            if not self._page:
                raise RuntimeError("No browser page is currently open. Call open_url first.")
            elements = self._page.evaluate(
                f"""() => {{
                    const candidates = Array.from(
                        document.querySelectorAll(
                            'button, a[href], input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="menuitem"]'
                        )
                    );
                    const items = [];
                    for (const el of candidates) {{
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') {{
                            continue;
                        }}
                        const role = el.getAttribute('role') || el.tagName.toLowerCase();
                        let name = el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') || el.value || '';
                        name = name.replace(/\\s+/g, ' ').trim();
                        if (!name && el.tagName.toLowerCase() === 'input') {{
                            name = el.getAttribute('name') || el.getAttribute('type') || '';
                        }}
                        if (name) {{
                            items.push({{
                                role: role,
                                name: name.slice(0, 80),
                                tag: el.tagName.toLowerCase(),
                                type: el.getAttribute('type') || null
                            }});
                        }}
                        if (items.length >= {max_elements}) break;
                    }}
                    return items;
                }}"""
            )
            return {
                "title": self._page.title(),
                "url": self._page.url,
                "elements": elements,
            }

    def click(self, target: str) -> dict[str, Any]:
        """Click an element by accessible name, visible text, or selector."""
        with self._lock:
            if not self._page:
                raise RuntimeError("No browser page is currently open. Call open_url first.")
            tgt = (target or "").strip()
            if not tgt:
                raise ValueError("Click target cannot be empty.")

            # Try by role/text first, then generic locator, then CSS selector
            clicked = False
            for locator in [
                self._page.get_by_role("button", name=tgt, exact=False),
                self._page.get_by_role("link", name=tgt, exact=False),
                self._page.get_by_text(tgt, exact=False),
                self._page.locator(tgt),
            ]:
                try:
                    if locator.count() > 0:
                        locator.first.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                raise ValueError(f"Could not find or click element matching '{tgt}'.")

            self._page.wait_for_timeout(1000)
            return {
                "clicked": tgt,
                "url": self._page.url,
                "title": self._page.title(),
            }

    def type_text(self, target: str, text: str) -> dict[str, Any]:
        """Fill or type text into an input or textarea."""
        with self._lock:
            if not self._page:
                raise RuntimeError("No browser page is currently open. Call open_url first.")
            tgt = (target or "").strip()
            typed = False
            for locator in [
                self._page.get_by_placeholder(tgt, exact=False),
                self._page.get_by_label(tgt, exact=False),
                self._page.get_by_role("textbox", name=tgt, exact=False),
                self._page.locator(tgt),
                self._page.locator("input[type='search'], input[type='text'], textarea").first,
            ]:
                try:
                    if locator.count() > 0:
                        locator.first.fill(text, timeout=3000)
                        locator.first.press("Enter")
                        typed = True
                        break
                except Exception:
                    continue

            if not typed:
                raise ValueError(f"Could not find input element matching '{tgt}' to type text.")

            self._page.wait_for_timeout(1000)
            return {
                "target": tgt,
                "text": text,
                "url": self._page.url,
            }

    def scroll(self, direction: str = "down") -> dict[str, Any]:
        """Scroll page up or down."""
        with self._lock:
            if not self._page:
                raise RuntimeError("No browser page is currently open.")
            dy = 500 if direction.lower() == "down" else -500
            self._page.mouse.wheel(0, dy)
            self._page.wait_for_timeout(500)
            return {"direction": direction, "url": self._page.url}

    def go_back(self) -> dict[str, Any]:
        """Navigate back."""
        with self._lock:
            if not self._page:
                raise RuntimeError("No browser page is currently open.")
            self._page.go_back(wait_until="domcontentloaded")
            return {"url": self._page.url, "title": self._page.title()}

    def close(self) -> None:
        """Close browser resources."""
        with self._lock:
            try:
                if self._page and not self._page.is_closed():
                    self._page.close()
                if self._context:
                    self._context.close()
                if self._browser:
                    self._browser.close()
                if self._playwright:
                    self._playwright.stop()
            except Exception as exc:
                logger.debug("Error closing browser: %s", exc)
            finally:
                self._page = None
                self._context = None
                self._browser = None
                self._playwright = None


# Global singleton instance
browser_manager = BrowserManager(headless=False)
