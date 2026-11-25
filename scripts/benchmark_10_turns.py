"""scripts/benchmark_10_turns.py

Run 10 benchmark turns through the system, measure performance,
and print out actual replies and the timing table.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient

from app.web.server import app

client = TestClient(app)

# Reset performance tracking
client.delete("/api/perf")

test_turns = [
    ("Nalla!", "greeting"),
    ("I have an idea for a brand new project, let's start today.", "new idea"),
    ("what time is it", "time query"),
    ("I'm so exhausted, nothing is working and I feel completely stuck.", "stressed message"),
    ("set reminder for client review at 4pm", "tool action request"),
    ("Did you turn on the water heater?", "device check / failure"),
    ("Explain MQTT to me simply in one sentence.", "simple question"),
    ("Let's rewrite the whole database in MongoDB right now.", "disagreement"),
    ("volume up", "volume control"),
    ("what is today's date", "date query"),
]

print("=== EXECUTING 10 TURNS ===")
for i, (msg, category) in enumerate(test_turns, 1):
    res = client.post("/api/chat", json={"message": msg, "session_id": "benchmark_sess"})
    data = res.json()
    reply = data.get("reply", "")
    metrics = data.get("metrics", {})
    print(f"Turn {i} [{category}]: \"{msg}\"")
    print(f"Reply: \"{reply}\"")
    print(f"Fast-path: {data.get('fast_path')}, Metrics: {metrics}")
    print("-" * 60)

perf_res = client.get("/api/perf").json()
print("\n=== PERF SUMMARY TABLE ===")
print(perf_res.get("summary", ""))
