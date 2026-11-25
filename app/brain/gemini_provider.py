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
_FALLBACK_MODELS    = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")


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
        temperature: float | None = None,
    ) -> None:
        self._api_key       = api_key or config.GEMINI_API_KEY
        self._chat_model    = model or config.GEMINI_CHAT_MODEL
        self._command_model = command_model or config.GEMINI_COMMAND_MODEL
        self._temperature   = temperature if temperature is not None else config.GEMINI_TEMPERATURE
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
        model: str | None = None,
    ) -> LLMResponse:
        """Send a full conversation to Gemini and return one response."""
        client = self._get_client()
        gemini_contents = _messages_to_gemini(messages)
        gemini_tools    = _tools_to_gemini(tools) if tools else None

        # Pick model: explicit model parameter > command_model (with tools) > chat_model
        if not model:
            model = self._command_model if gemini_tools else self._chat_model

        base_cfg: dict[str, Any] = {
            "temperature": self._temperature,
        }
        if system_prompt:
            base_cfg["system_instruction"] = system_prompt
        if gemini_tools:
            base_cfg["tools"] = gemini_tools
            base_cfg["max_output_tokens"] = _MAX_TOKENS_TOOLS
        else:
            base_cfg["max_output_tokens"] = _MAX_TOKENS_CHAT

        candidate_models = [model] + [m for m in _FALLBACK_MODELS if m != model]
        last_exc: Exception | None = None

        for cur_model in candidate_models:
            cfg = dict(base_cfg)
            if _is_thinking_model(cur_model):
                cfg["thinking_config"] = {"thinking_level": "minimal"}
            elif "thinking_config" in cfg:
                del cfg["thinking_config"]

            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    response = client.models.generate_content(
                        model=cur_model,
                        contents=gemini_contents,
                        config=cfg if cfg else None,
                    )
                    return _parse_response(response)
                except Exception as exc:
                    exc_str = str(exc)
                    retryable = any(
                        s in exc_str for s in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                    )
                    last_exc = exc
                    # If 503/UNAVAILABLE and we have a fallback model waiting, switch faster
                    if "503" in exc_str or "UNAVAILABLE" in exc_str:
                        if len(candidate_models) > 1 and cur_model != candidate_models[-1]:
                            logger.warning(
                                "Gemini model %s returned 503/UNAVAILABLE — falling back to next model…",
                                cur_model,
                            )
                            break
                    if retryable and attempt < _MAX_RETRIES:
                        wait = _BACKOFF_BASE ** attempt
                        logger.warning(
                            "Gemini %s on %s (attempt %d/%d) — retrying in %.0fs…",
                            exc_str[:60], cur_model, attempt, _MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                        continue
                    break

        msg = _friendly_error(last_exc)
        logger.error("Gemini generate failed across candidate models: %s", last_exc)
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
        primary_model = self._chat_model
        candidate_models = [primary_model] + [m for m in _FALLBACK_MODELS if m != primary_model]

        last_exc: Exception | None = None
        for cur_model in candidate_models:
            cfg: dict[str, Any] = {
                "max_output_tokens": _MAX_TOKENS_CHAT,
                "temperature": self._temperature,
            }
            if system_prompt:
                cfg["system_instruction"] = system_prompt
            if _is_thinking_model(cur_model):
                cfg["thinking_config"] = {"thinking_level": "minimal"}

            for attempt in range(1, _MAX_RETRIES + 1):
                yielded_any = False
                try:
                    stream_iter = client.models.generate_content_stream(
                        model=cur_model,
                        contents=gemini_contents,
                        config=cfg,
                    )
                    for chunk in stream_iter:
                        try:
                            text = chunk.text
                            if text:
                                yielded_any = True
                                yield text
                        except Exception:
                            continue
                    return  # Success
                except Exception as exc:
                    exc_str = str(exc)
                    last_exc = exc
                    if yielded_any:
                        # Cannot seamlessly fallback if tokens were already emitted to consumer
                        msg = _friendly_error(exc)
                        logger.error("Gemini stream interrupted after emitting chunks: %s", exc)
                        raise RuntimeError(msg) from exc

                    if ("503" in exc_str or "UNAVAILABLE" in exc_str) and cur_model != candidate_models[-1]:
                        logger.warning(
                            "Gemini stream %s on %s returned 503/UNAVAILABLE — falling back to next model…",
                            exc_str[:60], cur_model,
                        )
                        break

                    retryable = any(
                        s in exc_str for s in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                    )
                    if retryable and attempt < _MAX_RETRIES:
                        wait = _BACKOFF_BASE ** attempt
                        logger.warning(
                            "Gemini stream %s on %s (attempt %d/%d) — retrying in %.0fs…",
                            exc_str[:60], cur_model, attempt, _MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                        continue
                    break

        msg = _friendly_error(last_exc)
        logger.error("Gemini stream failed across candidate models: %s", last_exc)
        raise RuntimeError(msg) from last_exc

    def generate_multimodal(
        self,
        prompt: str,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Send an image + text prompt to Gemini multimodal model with instruction shield."""
        client = self._get_client()
        from google.genai import types

        chosen_model = model or self._chat_model
        candidate_models = [chosen_model] + [m for m in _FALLBACK_MODELS if m != chosen_model]

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=prompt),
                ],
            )
        ]

        cfg: dict[str, Any] = {
            "temperature": 0.2,
            "max_output_tokens": 600,
        }
        if system_prompt:
            cfg["system_instruction"] = system_prompt
        if _is_thinking_model(chosen_model):
            cfg["thinking_config"] = {"thinking_level": "minimal"}

        last_exc: Exception | None = None
        for cur_model in candidate_models:
            cur_cfg = dict(cfg)
            if _is_thinking_model(cur_model):
                cur_cfg["thinking_config"] = {"thinking_level": "minimal"}
            elif "thinking_config" in cur_cfg:
                del cur_cfg["thinking_config"]

            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    response = client.models.generate_content(
                        model=cur_model,
                        contents=contents,
                        config=cur_cfg,
                    )
                    return _parse_response(response)
                except Exception as exc:
                    exc_str = str(exc)
                    retryable = any(
                        s in exc_str for s in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                    )
                    last_exc = exc
                    if retryable and attempt < _MAX_RETRIES:
                        wait = _BACKOFF_BASE ** attempt
                        time.sleep(wait)
                        continue
                    break

        msg = _friendly_error(last_exc)
        logger.error("Gemini generate_multimodal failed across candidate models: %s", last_exc)
        raise RuntimeError(msg) from last_exc

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
