"""app/brain/gemini_provider.py

Google Gemini implementation of LLMProvider.

Optimizations in this version
-------------------------------
• Singleton client — created once at first use, never re-created.
• Warm-up — call warmup() once at startup for near-zero first-turn latency.
• thinking_level MINIMAL for chat (fastest); "low" for multi-step tool turns.
• max_output_tokens capped per call type (chat=150, with-tools=400).
• Tool schemas only attached when the caller passes them.
• True streaming via generate_content_stream — first token arrives faster.
• HTTP keep-alive: google-genai SDK reuses the underlying httpx session.
• Two model slots: chat_model (conversation) and command_model (tool calls).

Error handling
--------------
429 / quota exhausted  — exponential backoff (3 attempts, 2 s / 4 s / 8 s).
503 / server error     — same backoff policy.
Network / DNS failure  — caught and surfaced as a RuntimeError.
Empty / safety-blocked — returns a user-friendly LLMResponse.

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
from app import config

logger = logging.getLogger("nalla.gemini")

_MAX_RETRIES   = 3
_BACKOFF_BASE  = 2.0   # seconds; doubles each attempt

# Token limits by call type
_MAX_TOKENS_CHAT    = 150   # short conversational replies
_MAX_TOKENS_TOOLS   = 400   # tool-use turns need more room


def _is_thinking_model(model: str) -> bool:
    name = model.lower()
    return "gemini-2.5" in name or "gemini-3" in name


class GeminiProvider(LLMProvider):
    """LLMProvider backed by the Google Gemini API (google-genai SDK).

    Parameters
    ----------
    api_key:        Overrides config.GEMINI_API_KEY.
    model:          Chat / conversational model name.
    command_model:  Model for tool-use turns (defaults to same as model).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        command_model: str | None = None,
    ) -> None:
        self._api_key       = api_key or config.GEMINI_API_KEY
        self._chat_model    = model or config.GEMINI_CHAT_MODEL
        self._command_model = command_model or config.GEMINI_COMMAND_MODEL
        self._client        = None   # singleton, created on first use

    # ------------------------------------------------------------------
    # Warmup
    # ------------------------------------------------------------------

    def warmup(self) -> None:
        """Pre-connect and warm the Gemini client with a no-op call.

        Call once at startup from a background thread so the first real
        user turn doesn't pay TCP-handshake + TLS + model routing cost.
        """
        try:
            client = self._get_client()
            # Minimal non-reasoning call — just warms the HTTP connection.
            client.models.generate_content(
                model=self._chat_model,
                contents=[{"role": "user", "parts": [{"text": "hi"}]}],
                config={
                    "max_output_tokens": 1,
                    "system_instruction": "Reply with a single word.",
                },
            )
            logger.info("Gemini client warmed up (model: %s).", self._chat_model)
        except Exception as exc:
            logger.debug("Gemini warmup skipped: %s", exc)

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

        # Pick model: use command_model only when tool schemas are attached
        model = self._command_model if gemini_tools else self._chat_model

        cfg: dict[str, Any] = {}
        if system_prompt:
            cfg["system_instruction"] = system_prompt
        if gemini_tools:
            cfg["tools"] = gemini_tools
            cfg["max_output_tokens"] = _MAX_TOKENS_TOOLS
        else:
            cfg["max_output_tokens"] = _MAX_TOKENS_CHAT

        # Thinking level — MINIMAL for speed (thinking models only)
        if _is_thinking_model(model):
            cfg["thinking_config"] = {"thinking_level": "minimal"}

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=gemini_contents,
                    config=cfg if cfg else None,
                )
                return _parse_response(response)
            except Exception as exc:
                exc_str = str(exc)
                retryable = any(
                    s in exc_str for s in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                )
                if retryable and attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE ** attempt
                    logger.warning(
                        "Gemini %s (attempt %d/%d) — retrying in %.0fs…",
                        exc_str[:60], attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    last_exc = exc
                    continue
                last_exc = exc
                break

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

        Note: tool calling is not supported in streaming mode — this is
        called only for the final answer after any tool rounds.
        """
        client = self._get_client()
        gemini_contents = _messages_to_gemini(messages)

        # Always use chat_model for final streaming answer
        model = self._chat_model

        cfg: dict[str, Any] = {
            "max_output_tokens": _MAX_TOKENS_CHAT,
        }
        if system_prompt:
            cfg["system_instruction"] = system_prompt
        if _is_thinking_model(model):
            cfg["thinking_config"] = {"thinking_level": "minimal"}

        try:
            for chunk in client.models.generate_content_stream(
                model=model,
                contents=gemini_contents,
                config=cfg,
            ):
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
        """Return (and cache) the genai client singleton."""
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
            logger.info(
                "Gemini client initialised (chat: %s, command: %s)",
                self._chat_model, self._command_model,
            )
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
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)
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
            f"Model '{config.GEMINI_CHAT_MODEL}' not found (404). "
            "Update GEMINI_CHAT_MODEL in your .env to a valid model name."
        )
    if any(s in exc_str.lower() for s in ("network", "connection", "dns")):
        return "Network error — I cannot reach Gemini. Check your internet connection."
    return f"Gemini error: {exc_str[:200]}"
