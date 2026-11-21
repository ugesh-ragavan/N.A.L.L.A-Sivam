/**
 * app/web/static/js/pages/chat.js
 *
 * Chat Page Controller:
 *   • Modern message bubbles with user/assistant avatars.
 *   • Tool call inspection cards with arguments, results, and approval status.
 *   • Streaming response simulation.
 *   • Copy to clipboard & clear chat history.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  const chatHistory = [];

  function initChat() {
    const sendBtn = document.getElementById("chat-send-btn");
    const inputEl = document.getElementById("chat-user-input");
    const clearBtn = document.getElementById("chat-clear-btn");

    if (sendBtn && inputEl) {
      sendBtn.addEventListener("click", () => handleSendMessage());
      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          handleSendMessage();
        }
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        chatHistory.length = 0;
        const msgContainer = document.getElementById("chat-messages-container");
        if (msgContainer) {
          msgContainer.innerHTML = `<div style="text-align:center; color:var(--text-dim); margin-top:40px;">Chat cleared. Start a new conversation below.</div>`;
        }
        window.NallaApp.showToast("Conversation cleared.", "info");
      });
    }
  }

  async function handleSendMessage() {
    const inputEl = document.getElementById("chat-user-input");
    const text = inputEl ? inputEl.value.trim() : "";
    if (!text) return;

    inputEl.value = "";
    appendMessage("user", text);

    const loadingBubble = appendMessage("assistant", "...", true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: "web" }),
      });

      const data = await res.json();
      if (loadingBubble) loadingBubble.remove();

      if (res.ok && data.ok) {
        appendMessage("assistant", data.reply, false, data.tool_calls);
      } else {
        appendMessage("assistant", data.detail || data.reply || "Something went wrong.");
      }
    } catch (err) {
      if (loadingBubble) loadingBubble.remove();
      appendMessage("assistant", `Network error: ${err.message}`);
    }
  }

  function appendMessage(role, text, isLoading = false, toolCalls = null) {
    const container = document.getElementById("chat-messages-container");
    if (!container) return null;

    // Remove empty placeholder if present
    if (chatHistory.length === 0 && container.children.length === 1) {
      container.innerHTML = "";
    }

    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    const header = document.createElement("div");
    header.style.display = "flex";
    header.style.justifyContent = "space-between";
    header.style.marginBottom = "6px";
    header.style.fontSize = "0.75rem";
    header.style.color = "var(--text-dim)";
    header.style.fontFamily = "var(--font-mono)";

    const roleName = role === "user" ? "YOU" : "NALLA SIVAM";
    const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    header.innerHTML = `<span>${roleName}</span><span>${timeStr}</span>`;
    bubble.appendChild(header);

    // Tool calls card if present
    if (toolCalls && toolCalls.length > 0) {
      for (const tc of toolCalls) {
        const toolCard = document.createElement("div");
        toolCard.className = "tool-card";
        toolCard.innerHTML = `
          <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
            <span class="tool-name">⚡ ${window.NallaApp.escapeHtml(tc.name)}</span>
            <span class="badge ${tc.ok ? "badge-green" : "badge-red"}">${tc.ok ? "EXECUTED" : "FAILED"}</span>
          </div>
          <div style="color:var(--text-dim); margin-bottom:4px;">Args: <code>${window.NallaApp.escapeHtml(JSON.stringify(tc.args || {}))}</code></div>
          <div style="color:var(--text-main);">Result: ${window.NallaApp.escapeHtml(tc.result || tc.content || "")}</div>
        `;
        bubble.appendChild(toolCard);
      }
    }

    // Main text
    const textDiv = document.createElement("div");
    textDiv.className = "message-body";
    textDiv.innerHTML = formatMarkdown(text);
    bubble.appendChild(textDiv);

    // Copy action button
    if (!isLoading) {
      const copyBtn = document.createElement("button");
      copyBtn.className = "btn btn-sm btn-outline";
      copyBtn.style.marginTop = "8px";
      copyBtn.style.padding = "2px 8px";
      copyBtn.style.fontSize = "0.7rem";
      copyBtn.innerText = "📋 Copy";
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(text);
        window.NallaApp.showToast("Message copied!", "info");
      };
      bubble.appendChild(copyBtn);
    }

    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;

    chatHistory.push({ role, text });
    return bubble;
  }

  function formatMarkdown(text) {
    if (!text) return "";
    let s = window.NallaApp.escapeHtml(text);
    // Bold
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // Code inline
    s = s.replace(/`([^`]+)`/g, "<code style='background:rgba(0,0,0,0.4); padding:2px 4px; border-radius:3px;'>$1</code>");
    // Line breaks
    s = s.replace(/\n/g, "<br>");
    return s;
  }

  window.NallaPages.chat = {
    init: initChat,
    onActivate: () => {
      const input = document.getElementById("chat-user-input");
      if (input) input.focus();
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChat);
  } else {
    initChat();
  }
})();
