"""app/brain/llm_provider.py

Abstract LLM provider interface.

All LLM backends (Gemini, OpenAI, Ollama, …) must implement
LLMProvider so that the rest of the application never touches
provider-specific SDKs directly.  Swapping the backend is a
single config change.

Data classes
------------
ToolCall    — a parsed request from the model to invoke a named tool.
LLMResponse — the model's reply: either text, one-or-more tool calls, or both.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Generator


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM.

    Attributes
    ----------
    id:   Provider-assigned call ID (used to match results back).
    name: The registered tool name (must exist in ToolRegistry).
    args: Keyword arguments to pass to the tool function.
    """
    id:   str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """The complete response from one model call.

    Either ``text`` or ``tool_calls`` (or both) may be present.
    An empty response with neither is treated as an error by the agent loop.

    Attributes
    ----------
    text:         Textual reply from the model (may be empty string).
    tool_calls:   List of tool calls the model wants to make.
    finish_reason: Provider finish reason string (e.g. "stop", "tool_calls",
                   "max_tokens", "safety").
    """
    text:          str               = ""
    tool_calls:    list[ToolCall]    = field(default_factory=list)
    finish_reason: str               = "stop"
    raw_content:   Any               = None

    # Convenience helpers --------------------------------------------------

    @property
    def has_tool_calls(self) -> bool:
        """True when the model wants to invoke at least one tool."""
        return bool(self.tool_calls)

    @property
    def is_empty(self) -> bool:
        """True when neither text nor tool calls were returned."""
        return not self.text and not self.tool_calls


# ---------------------------------------------------------------------------
# Message format
# ---------------------------------------------------------------------------

# A conversation message dict understood by all providers.
# Keys: "role" ("user" | "assistant" | "tool"), "content" (str),
#       and optionally "tool_call_id" for tool-result messages.
Message = dict[str, Any]


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class LLMProvider(abc.ABC):
    """Interface that every LLM backend must implement.

    Implementations should be stateless (no stored conversation history);
    the caller passes the full message list on every call.
    """

    # ------------------------------------------------------------------
    # Required abstract methods
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Send messages to the model and return a complete response.

        Parameters
        ----------
        messages:      Conversation history in the provider-agnostic format.
        tools:         Optional list of tool schemas in provider-agnostic JSON
                       format (name, description, parameters).
        system_prompt: System / persona text prepended to the conversation.

        Returns
        -------
        LLMResponse with text and/or tool_calls populated.

        Raises
        ------
        RuntimeError: on unrecoverable provider errors (after retries).
        """

    @abc.abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> Generator[str, None, None]:
        """Yield text chunks as they arrive from the model.

        Tool-calling is not supported in streaming mode; the agent loop
        should use ``generate()`` when tools are registered.

        Yields
        ------
        str chunks of the reply text.
        """
