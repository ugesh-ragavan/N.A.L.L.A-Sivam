/**
 * app/web/static/js/pages/chat.js
 *
 * Chat Page Controller:
 *   • Modern message bubbles with user/assistant avatars.
 *   • Real-time SSE token streaming from /api/chat/stream.
 *   • Tool call inspection cards with arguments, results, and approval status (GREEN, AMBER, RED).
 *   • Safe markdown rendering and automatic suppression of expressive audio tags (<chuckle>, etc.).
 *   • Copy to clipboard & clear chat history.
 *   • Interactive test trigger for AMBER actions with modal confirmation.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  const chatHistory = [];

  function initChat() {
    const sendBtn = document.getElementById("chat-send-btn");
    const inputEl = document.getElementById("chat-user-input");
    const clearBtn = document.getElementById("chat-clear-btn");
    const testAmberBtn = document.getElementById("chat-test-amber-btn");

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
          msgContainer.innerHTML = `<div style="text-align:center; color:var(--text-dim); margin-top:40px;">Conversation cleared. Start a new conversation below.</div>`;
        }
        window.NallaApp.showToast("Conversation cleared.", "info");
      });
    }

    if (testAmberBtn) {
      testAmberBtn.addEventListener("click", () => triggerAmberTest());
    }
  }

  function stripExpressiveTags(text) {
    if (!text) return "";
    return text.replace(/<(chuckle|sigh|short pause|pause|giggle|groan|laughter|cough|throat clearing|yawn)>/gi, "").replace(/\s+/g, " ");
  }

  function setInput(text) {
    const inputEl = document.getElementById("chat-user-input");
    if (inputEl) {
      inputEl.value = text;
      inputEl.focus();
    }
  }

  async function handleSendMessage(presetText = null) {
    const inputEl = document.getElementById("chat-user-input");
    const text = (presetText !== null ? presetText : (inputEl ? inputEl.value : "")).trim();
    if (!text) return;

    if (inputEl && presetText === null) inputEl.value = "";
    appendMessage("user", text);

    const assistantBubble = appendMessage("assistant", "", true);
    const bodyEl = assistantBubble.querySelector(".message-body");

    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: "web" }),
      });

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let accumulatedText = "";
      let toolCalls = [];

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop();

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const jsonStr = trimmed.slice(5).trim();
          try {
            const data = JSON.parse(jsonStr);
            if (data.type === "chunk") {
              accumulatedText += stripExpressiveTags(data.text);
              if (bodyEl) {
                bodyEl.innerHTML = formatMarkdown(accumulatedText);
              }
              const container = document.getElementById("chat-messages-container");
              if (container) container.scrollTop = container.scrollHeight;
            } else if (data.type === "done") {
              if (data.reply) {
                accumulatedText = stripExpressiveTags(data.reply);
              }
              toolCalls = data.tool_calls || [];
            }
          } catch (e) {
            console.error("SSE parse error", e);
          }
        }
      }

      finalizeBubble(assistantBubble, accumulatedText, toolCalls);
    } catch (err) {
      if (bodyEl) {
        bodyEl.innerHTML = `<span style="color:var(--red);">Network error: ${window.NallaApp.escapeHtml(err.message)}</span>`;
      }
      finalizeBubble(assistantBubble, bodyEl ? bodyEl.innerText : "Error", []);
    }
  }

  async function triggerAmberTest() {
    window.NallaApp.showToast("Triggering AMBER test action...", "info");
    try {
      const res = await fetch("/api/test/amber", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: "Toggle Living Room Relay 1 (AMBER risk)" }),
      });
      const data = await res.json();
      
      const tc = {
        name: data.tool || "fake_relay_toggle",
        args: { relay_id: "relay_1", state: "on" },
        risk: "amber",
        approved: data.approved,
        ok: data.ok,
        result: data.message || (data.approved ? "Relay toggled to on." : "Action denied by user."),
      };

      const replyText = data.approved
        ? "Action approved. Relay 1 has been turned on."
        : "Action was denied. No changes were made.";

      appendMessage("assistant", replyText, false, [tc]);
      window.NallaApp.showToast(data.approved ? "Relay toggled successfully." : "Action denied.", data.approved ? "success" : "alert");
    } catch (err) {
      window.NallaApp.showToast(`Error executing test: ${err.message}`, "alert");
    }
  }

  function appendMessage(role, text, isLoading = false, toolCalls = null) {
    const container = document.getElementById("chat-messages-container");
    if (!container) return null;

    if (chatHistory.length === 0 && container.children.length === 1) {
      container.innerHTML = "";
    }

    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    if (isLoading) bubble.classList.add("loading");

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

    // Tool cards if already present
    if (toolCalls && toolCalls.length > 0) {
      renderToolCards(bubble, toolCalls);
    }

    // Message body
    const textDiv = document.createElement("div");
    textDiv.className = "message-body";
    textDiv.innerHTML = isLoading ? "<span style='color:var(--text-dim);'>...</span>" : formatMarkdown(stripExpressiveTags(text));
    bubble.appendChild(textDiv);

    // Copy button
    if (!isLoading) {
      addCopyButton(bubble, stripExpressiveTags(text));
    }

    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;

    if (!isLoading) {
      chatHistory.push({ role, text: stripExpressiveTags(text) });
    }
    return bubble;
  }

  function finalizeBubble(bubble, text, toolCalls) {
    if (!bubble) return;
    bubble.classList.remove("loading");

    const bodyEl = bubble.querySelector(".message-body");
    const cleanText = stripExpressiveTags(text) || "(Empty response)";

    if (bodyEl) {
      bodyEl.innerHTML = formatMarkdown(cleanText);
    }

    if (toolCalls && toolCalls.length > 0) {
      renderToolCards(bubble, toolCalls, bodyEl);
    }

    addCopyButton(bubble, cleanText);

    const container = document.getElementById("chat-messages-container");
    if (container) container.scrollTop = container.scrollHeight;

    chatHistory.push({ role: "assistant", text: cleanText });
  }

  function renderToolCards(bubble, toolCalls, insertBeforeEl = null) {
    for (const tc of toolCalls) {
      const toolCard = document.createElement("div");
      toolCard.className = "tool-card";

      const risk = (tc.risk || "green").toLowerCase();
      const riskBadgeClass = risk === "red" ? "badge-red" : (risk === "amber" ? "badge-amber" : "badge-green");
      const riskBadge = `<span class="badge ${riskBadgeClass}">${risk.toUpperCase()}</span>`;

      let approvalBadge = "";
      if (tc.approved === false) {
        approvalBadge = `<span class="badge badge-red">DENIED</span>`;
      } else if (tc.approved === true && risk !== "green") {
        approvalBadge = `<span class="badge badge-amber">APPROVED</span>`;
      }

      const execBadge = `<span class="badge ${tc.ok ? 'badge-green' : 'badge-red'}">${tc.ok ? 'EXECUTED' : 'FAILED'}</span>`;

      toolCard.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span class="tool-name">⚡ ${window.NallaApp.escapeHtml(tc.name)}</span>
          <div style="display:flex; gap:6px;">
            ${riskBadge}
            ${approvalBadge}
            ${execBadge}
          </div>
        </div>
        <div style="color:var(--text-dim); margin-bottom:4px; font-size:0.75rem;">Args: <code>${window.NallaApp.escapeHtml(JSON.stringify(tc.args || {}))}</code></div>
        <div style="color:var(--text-main); font-size:0.8rem;">Result: ${window.NallaApp.escapeHtml(tc.result || tc.content || "")}</div>
      `;

      if (insertBeforeEl) {
        bubble.insertBefore(toolCard, insertBeforeEl);
      } else {
        bubble.appendChild(toolCard);
      }
    }
  }

  function addCopyButton(bubble, text) {
    // Avoid duplicate copy buttons
    if (bubble.querySelector(".chat-copy-btn")) return;
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn btn-sm btn-outline chat-copy-btn";
    copyBtn.style.marginTop = "8px";
    copyBtn.style.padding = "2px 8px";
    copyBtn.style.fontSize = "0.7rem";
    copyBtn.innerText = "📋 Copy";
    copyBtn.onclick = () => {
      navigator.clipboard.writeText(text);
      window.NallaApp.showToast("Message copied to clipboard!", "info");
    };
    bubble.appendChild(copyBtn);
  }

  function formatMarkdown(text) {
    if (!text) return "";
    let s = window.NallaApp.escapeHtml(text);
    // Bold
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // Italic
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    // Inline code
    s = s.replace(/`([^`]+)`/g, "<code style='background:rgba(0,0,0,0.4); padding:2px 4px; border-radius:3px;'>$1</code>");
    // Line breaks
    s = s.replace(/\n/g, "<br>");
    return s;
  }

  window.NallaPages.chat = {
    init: initChat,
    sendMessage: handleSendMessage,
    setInput: setInput,
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
