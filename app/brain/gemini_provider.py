"""app/brain/gemini_provider.py

Google Gemini implementation of LLMProvider.

Uses the ``google-genai`` SDK.  The model name is always read from
``GEMINI_MODEL`` config — never hardcoded here.

Error handling
--------------
429 / quota exhausted  — exponential backoff (3 attempts, 2 s / 4 s / 8 s).
503 / server error     — same backoff policy.
Network / DNS failure  — caught and surfaced as a RuntimeError with a
                         clear message, not a bare traceback.
Empty / safety-blocked — returns an LLMResponse with a user-friendly
                         explanation in the ``text`` field.

Tool schema translation
-----------------------
Provider-agnostic schema  →  Gemini FunctionDeclaration  (before the call).
Gemini FunctionCall       →  our ToolCall dataclass       (after the call).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Generator

from app.brain.llm_provider import LLMProvider, LLMResponse, Message, ToolCall
from app.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger("nalla.gemini")

_MAX_RETRIES   = 3
_BACKOFF_BASE  = 2.0   # seconds; doubles each attempt


class GeminiProvider(LLMProvider):
    """LLMProvider backed by the Google Gemini API (google-genai SDK).

    The client is created lazily on first use so that import-time errors
    are avoided when the SDK is not installed or the key is missing.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key or GEMINI_API_KEY
        self._model   = model   or GEMINI_MODEL
        self._client  = None    # lazy init

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Send a full conversation to Gemini and return one response."""
        client = self._get_client()
        gemini_contents = _messages_to_gemini(messages)
        gemini_tools    = _tools_to_gemini(tools) if tools else None
        config          = {}
        if system_prompt:
            config["system_instruction"] = system_prompt
        if gemini_tools:
            config["tools"] = gemini_tools

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                kwargs: dict[str, Any] = {"model": self._model, "contents": gemini_contents}
                if config:
                    kwargs["config"] = config

                response = client.models.generate_content(**kwargs)
                return _parse_response(response)

            except Exception as exc:
                exc_str = str(exc)
                retryable = "429" in exc_str or "503" in exc_str or "UNAVAILABLE" in exc_str or "RESOURCE_EXHAUSTED" in exc_str

                if retryable and attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE ** attempt
                    logger.warning(
                        "Gemini %s (attempt %d/%d) — retrying in %.0fs…",
                        exc_str[:60], attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    last_exc = exc
                    continue

                # Non-retryable or final attempt
                last_exc = exc
                break

        # All retries exhausted — surface a clear error.
        msg = _friendly_error(last_exc)
        logger.error("Gemini generate failed after %d attempts: %s", _MAX_RETRIES, last_exc)
        raise RuntimeError(msg) from last_exc

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> Generator[str, None, None]:
        """Yield text chunks from a streaming Gemini response.

        Note: tool calling is not supported in streaming mode.
        """
        client = self._get_client()
        gemini_contents = _messages_to_gemini(messages)
        config: dict[str, Any] = {}
        if system_prompt:
            config["system_instruction"] = system_prompt

        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "contents": gemini_contents,
            }
            if config:
                kwargs["config"] = config

            for chunk in client.models.generate_content_stream(**kwargs):
                try:
                    text = chunk.text
                    if text:
                        yield text
                except Exception:
                    continue
        except Exception as exc:
            msg = _friendly_error(exc)
            logger.error("Gemini stream failed: %s", exc)
            raise RuntimeError(msg) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return (and cache) the genai client, checking the API key first."""
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to your .env file. "
                "Get a free key at https://aistudio.google.com/"
            )
        try:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
            logger.info("Gemini client initialised (model: %s)", self._model)
        except ImportError as exc:
            raise RuntimeError(
                "google-genai SDK is not installed. Run: pip install google-genai"
            ) from exc
        return self._client


# ---------------------------------------------------------------------------
# Schema / message translation helpers
# ---------------------------------------------------------------------------

def _messages_to_gemini(messages: list[Message]) -> list[Any]:
    """Convert our generic Message list to Gemini ``contents`` format."""
    contents = []
    for msg in messages:
        if "_raw_content" in msg and msg["_raw_content"] is not None:
            contents.append(msg["_raw_content"])
            continue

        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Tool result messages are sent as user turns with function responses.
        if role == "tool":
            contents.append({
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": msg.get("name", ""),
                        "response": {"result": content},
                    }
                }],
            })
        else:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": str(content)}]})

    return contents


def _tools_to_gemini(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert our generic tool schemas to Gemini FunctionDeclaration format."""
    declarations = []
    for t in tools:
        fd: dict[str, Any] = {
            "name": t["name"],
            "description": t.get("description", ""),
        }
        params = t.get("parameters", {})
        if params and params.get("properties"):
            fd["parameters"] = params
        declarations.append(fd)
    return [{"function_declarations": declarations}]


def _parse_response(response: Any) -> LLMResponse:
    """Convert a Gemini GenerateContentResponse to our LLMResponse."""
    # Safety / blocked check
    try:
        finish = getattr(response, "prompt_feedback", None)
        if finish and getattr(finish, "block_reason", None):
            reason = finish.block_reason
            return LLMResponse(
                text=f"I can't respond to that. (Safety block: {reason})",
                finish_reason="safety",
            )
    except Exception:
        pass

    # Extract text and function calls from candidates
    text_parts: list[str] = []
    tool_calls:  list[ToolCall] = []
    finish_reason = "stop"

    try:
        candidates = getattr(response, "candidates", [])
        if not candidates:
            return LLMResponse(
                text="(No response generated. The model returned an empty reply.)",
                finish_reason="empty",
            )

        candidate = candidates[0]
        finish_reason = str(getattr(candidate, "finish_reason", "stop")).lower()

        for part in getattr(candidate.content, "parts", []):
            # Text part
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)

            # Function call part
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=   getattr(fc, "id",   fc.name),
                    name= fc.name,
                    args= dict(fc.args) if fc.args else {},
                ))

    except Exception as exc:
        logger.warning("Error parsing Gemini response: %s", exc)
        return LLMResponse(
            text="(I had trouble reading the model's response. Please try again.)",
            finish_reason="parse_error",
        )

    text = "".join(text_parts)
    raw_content = getattr(candidate, "content", None)
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        raw_content=raw_content,
    )


def _friendly_error(exc: Exception | None) -> str:
    """Convert a raw exception into a user-friendly error string."""
    if exc is None:
        return "An unknown error occurred."
    exc_str = str(exc)
    if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
        return (
            "Gemini quota exhausted — I've hit the free-tier limit. "
            "Try again in a few seconds or check your usage at aistudio.google.com."
        )
    if "503" in exc_str or "UNAVAILABLE" in exc_str:
        return "Gemini is temporarily unavailable (503). Please try again shortly."
    if "404" in exc_str or "NOT_FOUND" in exc_str:
        return (
            f"Model '{GEMINI_MODEL}' not found (404). "
            "Update GEMINI_MODEL in your .env to a valid model name."
        )
    if "network" in exc_str.lower() or "connection" in exc_str.lower() or "dns" in exc_str.lower():
        return "Network error — I cannot reach Gemini. Check your internet connection."
    return f"Gemini error: {exc_str[:200]}"
