from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

from app.brain.browser import BrowserManager, is_consequential_target
from app.brain.tools import registry, ToolResult
from app.core.approvals import ApprovalManager, RiskLevel
from app.brain.agent import _looks_like_action, Agent
from app.brain.llm_provider import LLMProvider, LLMResponse, ToolCall
from app.brain.persona import Persona
from app.memory.store import MemoryStore


class FakeProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return LLMResponse(text="Done.")

    def stream(self, messages, tools=None, system_prompt=None):
        yield self.generate(messages, tools, system_prompt).text


def test_url_validation_accepts_valid_urls():
    assert BrowserManager.validate_url("https://example.com") == "https://example.com"
    assert BrowserManager.validate_url("http://127.0.0.1:8000/test") == "http://127.0.0.1:8000/test"


@pytest.mark.parametrize(
    "bad_url",
    [
        "example.com",
        "file:///C:/test.html",
        "javascript:alert(1)",
        "https://user:secret@example.com",
        "   ",
        "",
        "ftp://files.example.com",
    ],
)
def test_url_validation_rejects_unsafe_urls(bad_url: str):
    with pytest.raises(ValueError):
        BrowserManager.validate_url(bad_url)


def test_consequential_target_detection():
    assert is_consequential_target("Submit order") is True
    assert is_consequential_target("Buy now") is True
    assert is_consequential_target("delete my account") is True
    assert is_consequential_target("Pay $99") is True
    assert is_consequential_target("Confirm payment") is True
    assert is_consequential_target("Terminate session") is True

    assert is_consequential_target("Search the web") is False
    assert is_consequential_target("Read article") is False
    assert is_consequential_target("Next page") is False
    assert is_consequential_target("About Us") is False


def test_browser_tools_registered_in_registry():
    expected_tools = [
        "browser_open_url",
        "browser_search",
        "browser_read_page",
        "browser_inspect_page",
        "browser_click",
        "browser_consequential_click",
        "browser_type_text",
        "browser_scroll",
        "browser_go_back",
        "browser_close",
    ]
    for name in expected_tools:
        entry = registry.get(name)
        assert entry is not None, f"Tool '{name}' should be registered."
        if name == "browser_consequential_click":
            assert entry.risk == RiskLevel.AMBER
        else:
            assert entry.risk == RiskLevel.GREEN


def test_action_regex_recognizes_browsing():
    assert _looks_like_action("open youtube") is True
    assert _looks_like_action("browse python documentation") is True
    assert _looks_like_action("search google for weather") is True
    assert _looks_like_action("click on the download button") is True
    assert _looks_like_action("type hello into search box") is True


class _MockWebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = """<!DOCTYPE html>
<html>
<head><title>Nalla Browser Test</title></head>
<body>
    <h1>Welcome to Nalla Web Test</h1>
    <p>This is a test page for browser automation.</p>
    <label for="q">Search Box</label>
    <input id="q" type="text" placeholder="Search topics...">
    <button id="btn-regular">Explore</button>
    <button id="btn-submit">Submit order</button>
</body>
</html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def test_browser_automation_with_local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockWebHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    from app.brain.browser import browser_manager
    # Ensure headless for CI/test run
    browser_manager.headless = True

    try:
        url = f"http://127.0.0.1:{port}/"
        res_open = registry.run("browser_open_url", {"url": url})
        assert res_open.ok is True
        assert "Nalla Browser Test" in res_open.message

        res_read = registry.run("browser_read_page", {})
        assert res_read.ok is True
        assert "Welcome to Nalla Web Test" in res_read.message

        res_inspect = registry.run("browser_inspect_page", {})
        assert res_inspect.ok is True
        assert len(res_inspect.data["elements"]) >= 3

        res_type = registry.run("browser_type_text", {"target": "Search topics...", "text": "Raspberry Pi"})
        assert res_type.ok is True

        # Safe click on "Explore" should succeed
        res_click_safe = registry.run("browser_click", {"target": "Explore"})
        assert res_click_safe.ok is True

        # Consequential click on "Submit order" via browser_click must be blocked
        res_click_consequential_blocked = registry.run("browser_click", {"target": "Submit order"})
        assert res_click_consequential_blocked.ok is False
        assert "browser_consequential_click" in res_click_consequential_blocked.message

        # Consequential click via browser_consequential_click succeeds
        res_click_consequential = registry.run("browser_consequential_click", {"target": "Submit order"})
        assert res_click_consequential.ok is True

    finally:
        registry.run("browser_close", {})
        server.shutdown()


def test_agent_approval_flow_for_consequential_click():
    """Verify that agent loop enforces AMBER approval on browser_consequential_click."""
    approval_history: list[tuple[str, RiskLevel]] = []

    def tracking_callback(desc: str, risk: RiskLevel) -> bool:
        approval_history.append((desc, risk))
        return True

    approvals = ApprovalManager(callback=tracking_callback)

    # Fake provider that first requests browser_consequential_click then responds
    fake_provider = FakeProvider([
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="call_1", name="browser_consequential_click", args={"target": "Submit order"})],
        ),
        LLMResponse(text="Order submitted successfully, boss."),
    ])

    agent = Agent(
        provider=fake_provider,
        registry=registry,
        persona=Persona(),
        approvals=approvals,
    )

    reply = agent.chat([{"role": "user", "content": "click submit order on the page"}])
    assert "Order submitted" in reply
    assert len(approval_history) == 1
    assert approval_history[0][1] == RiskLevel.AMBER
    assert "browser_consequential_click" in approval_history[0][0]
