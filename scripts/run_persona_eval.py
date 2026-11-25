"""scripts/run_persona_eval.py

Live model evaluation runner for Persona v3.
Runs 12 representative prompts across categories and prints the outputs to judge warmth and rhythm.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from app import config
from app.brain.agent import Agent
from app.brain.gemini_provider import GeminiProvider
from app.brain.persona import Persona
from app.brain.tools import registry
from app.core.approvals import ApprovalManager
from app.memory.store import store

SAMPLE_12_PROMPTS = [
    ("Quick Fact", "capital of Australia?"),
    ("Chitchat", "thanks"),
    ("Tanglish Casual", "machan, innaiku mood illa"),
    ("Tanglish Technical", "bro indha error enna da? line 42 index out of range"),
    ("Stressed Message", "I'm so exhausted, nothing is working and I feel completely stuck."),
    ("Debug Help", "my esp32 keeps rebooting"),
    ("Explanation", "explain embeddings"),
    ("Uncertain Fact", "latest iPhone price in India?"),
    ("Correction of Mistake", "Can we store millions of records by calling SQLite vacuum in a loop?"),
    ("Mis-heard Tanglish Command", "ayo pathukalam, turn on light"),
    ("Are you human?", "are you human?"),
    ("Late-night Task Question", "what is our next todo?"),
]


def run_eval():
    print("=====================================================================")
    print("           NALLA SIVAM Persona v3 Live Evaluation (12 Prompts)       ")
    print("=====================================================================")
    provider = GeminiProvider()
    persona = Persona()
    approvals = ApprovalManager(callback=lambda desc, risk: True)  # Auto-approve AMBER for non-blocking evaluation
    agent = Agent(provider=provider, registry=registry, persona=persona, approvals=approvals, store=store)

    for category, prompt in SAMPLE_12_PROMPTS:
        print(f"\n--- [{category}] ---")
        print(f"User: {prompt}")
        try:
            reply = agent.chat([{"role": "user", "content": prompt}])
            print(f"Nalla: {reply}")
        except Exception as exc:
            print(f"Nalla (Error): {exc}")
    print("\n=====================================================================")


if __name__ == "__main__":
    run_eval()
