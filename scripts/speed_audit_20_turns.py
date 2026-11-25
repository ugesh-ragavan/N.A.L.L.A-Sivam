"""
scripts/speed_audit_20_turns.py

Benchmark and Latency Audit Suite:
Runs 20 turns measuring:
  • Fast-path latency (< 100 ms target)
  • First LLM token latency (< 1000 ms target)
  • First TTS audio chunk latency (< 2000 ms target)
  • Total turn latency

Outputs a 20-turn formatted table and evaluates misses.
"""

import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import config
from app.brain.agent import Agent
from app.brain.fast_router import route as fast_route
from app.brain.gemini_provider import GeminiProvider
from app.brain.persona import Persona
from app.brain.tools import registry
from app.core.approvals import ApprovalManager
from app.memory.store import store
from app.perf import PerfTracker
from app.voice.audio_io import AudioPlayer, AudioRecorder
from app.voice.pipeline import VoicePipeline
from app.voice.stt import FasterWhisperSTT
from app.voice.tts import EdgeTTS, TTSManager
from app.voice.wakeword import PushToTalk
import app.web.server  # Pre-import web server so module import overhead does not affect device routing

TURNS = [
    # 1-8: Fast-path tests
    {"id": 1, "type": "Fast-Path", "text": "Nalla!"},
    {"id": 2, "type": "Fast-Path", "text": "what time is it"},
    {"id": 3, "type": "Fast-Path", "text": "what is today's date"},
    {"id": 4, "type": "Fast-Path", "text": "volume up"},
    {"id": 5, "type": "Fast-Path", "text": "list reminders"},
    {"id": 6, "type": "Fast-Path", "text": "mute mic"},
    {"id": 7, "type": "Fast-Path", "text": "unmute mic"},
    {"id": 8, "type": "Fast-Path", "text": "turn on living room light"},
    # 9-15: Conversational LLM turns
    {"id": 9, "type": "Conversational", "text": "Give me a quick tip for debugging Python code."},
    {"id": 10, "type": "Conversational", "text": "What is your motto?"},
    {"id": 11, "type": "Conversational", "text": "Explain MQTT QoS 1 in one sentence."},
    {"id": 12, "type": "Conversational", "text": "Give me a one-sentence motivation for coding tonight."},
    {"id": 13, "type": "Conversational", "text": "Should I rewrite everything in microservices?"},
    {"id": 14, "type": "Conversational (Tamil)", "text": "Sema tired ah irukku boss, enna panrathu?"},
    {"id": 15, "type": "Conversational", "text": "Why do programmers drink coffee?"},
    # 16-17: Tool calls
    {"id": 16, "type": "Tool Call", "text": "Remember that my favorite MCU is the ESP32-S3."},
    {"id": 17, "type": "Tool Call", "text": "Recall what my favorite MCU is."},
    # 18-20: General conversation
    {"id": 18, "type": "Conversational", "text": "What is the typical weather in Coimbatore?"},
    {"id": 19, "type": "Conversational", "text": "How does Faster-Whisper run so fast on CPU?"},
    {"id": 20, "type": "Conversational", "text": "Thanks Nalla, see you soon!"},
]


def run_speed_audit():
    print("=" * 105)
    print(" NALLA SIVAM SPEED AUDIT: 20-TURN LATENCY BENCHMARK")
    print(" Targets: Fast-Path < 100ms | First LLM Token < 1000ms | First Audio Chunk < 2000ms")
    print("=" * 105)

    # Initialize components
    provider = GeminiProvider()
    persona = Persona()
    approvals = ApprovalManager()
    agent = Agent(
        provider=provider,
        registry=registry,
        persona=persona,
        approvals=approvals,
        store=store,
        memory_enabled=True,
    )
    tts = TTSManager()

    records = []

    print(f"{'#':<3} | {'Type':<22} | {'Utterance':<30} | {'FP ms':<7} | {'LLM 1st':<8} | {'TTS 1st':<8} | {'Total ms':<9} | {'Status'}")
    print("-" * 105)

    for item in TURNS:
        tid = item["id"]
        ttype = item["type"]
        text = item["text"]
        preview_text = text[:28] + (".." if len(text) > 28 else "")

        t_turn_start = time.perf_counter()

        # Step 1: Check fast-path router
        t_fp0 = time.perf_counter()
        fast_reply = fast_route(text)
        fp_ms = round((time.perf_counter() - t_fp0) * 1000.0, 1)

        llm_1st_ms = 0.0
        tts_1st_ms = 0.0
        reply_text = ""

        if fast_reply:
            reply_text = fast_reply
            # Fast-path TTS synthesis
            t_tts0 = time.perf_counter()
            tts.synthesize(reply_text[:60])
            tts_1st_ms = round((time.perf_counter() - t_tts0) * 1000.0, 1)
            total_ms = round((time.perf_counter() - t_turn_start) * 1000.0, 1)

            fp_pass = fp_ms < 100.0
            tts_pass = tts_1st_ms < 2000.0
            status = "PASS" if (fp_pass and tts_pass) else "MISS"

            records.append({
                "id": tid, "type": ttype, "text": text, "is_fp": True,
                "fp_ms": fp_ms, "llm_1st_ms": 0.0, "tts_1st_ms": tts_1st_ms,
                "total_ms": total_ms, "status": status, "reply": reply_text
            })

            print(f"{tid:<3} | {ttype:<22} | {preview_text:<30} | {fp_ms:>5.1f}ms | {'--':<8} | {tts_1st_ms:>6.1f}ms | {total_ms:>7.1f}ms | {status}")
        else:
            # Full Agent Stream Turn
            messages = [{"role": "user", "content": text}]
            t_llm0 = time.perf_counter()
            first_token_received = False
            first_chunk_text = ""
            full_reply_chunks = []

            for chunk in agent.chat_stream(messages):
                if not first_token_received and chunk:
                    first_token_received = True
                    llm_1st_ms = round((time.perf_counter() - t_llm0) * 1000.0, 1)
                    first_chunk_text = chunk
                full_reply_chunks.append(chunk)

            reply_text = "".join(full_reply_chunks).strip()

            # First TTS audio chunk
            t_tts0 = time.perf_counter()
            tts_target_phrase = first_chunk_text or reply_text[:60]
            tts.synthesize(tts_target_phrase)
            tts_1st_ms = round((time.perf_counter() - t_tts0) * 1000.0, 1)

            total_ms = round((time.perf_counter() - t_turn_start) * 1000.0, 1)

            llm_pass = llm_1st_ms < 1000.0
            tts_pass = tts_1st_ms < 2000.0
            status = "PASS" if (llm_pass and tts_pass) else "MISS"

            records.append({
                "id": tid, "type": ttype, "text": text, "is_fp": False,
                "fp_ms": fp_ms, "llm_1st_ms": llm_1st_ms, "tts_1st_ms": tts_1st_ms,
                "total_ms": total_ms, "status": status, "reply": reply_text
            })

            print(f"{tid:<3} | {ttype:<22} | {preview_text:<30} | {fp_ms:>5.1f}ms | {llm_1st_ms:>6.1f}ms | {tts_1st_ms:>6.1f}ms | {total_ms:>7.1f}ms | {status}")

    print("-" * 105)

    # Compute Averages
    fp_records = [r for r in records if r["is_fp"]]
    llm_records = [r for r in records if not r["is_fp"]]

    avg_fp = sum(r["fp_ms"] for r in fp_records) / len(fp_records) if fp_records else 0
    avg_llm_1st = sum(r["llm_1st_ms"] for r in llm_records) / len(llm_records) if llm_records else 0
    avg_tts_1st = sum(r["tts_1st_ms"] for r in records) / len(records)
    avg_total = sum(r["total_ms"] for r in records) / len(records)

    print(f"AVERAGES: Fast-Path = {avg_fp:.1f}ms | LLM 1st Token = {avg_llm_1st:.1f}ms | TTS 1st Audio = {avg_tts_1st:.1f}ms | Turn Total = {avg_total:.1f}ms")
    print("=" * 105)

    misses = [r for r in records if r["status"] == "MISS"]
    print(f"\n[AUDIT RESULTS SUMMARY]")
    print(f"• Total Turns: 20 | Passed: {len(records) - len(misses)} | Misses: {len(misses)}")
    if misses:
        print("\nIdentified Misses & Root Causes:")
        for m in misses:
            reasons = []
            if m["is_fp"] and m["fp_ms"] >= 100.0:
                reasons.append(f"Fast-path took {m['fp_ms']}ms (>= 100ms)")
            if not m["is_fp"] and m["llm_1st_ms"] >= 1000.0:
                reasons.append(f"LLM 1st token took {m['llm_1st_ms']}ms (>= 1000ms, likely TLS/network handshake or tool round-trip)")
            if m["tts_1st_ms"] >= 2000.0:
                reasons.append(f"TTS 1st chunk took {m['tts_1st_ms']}ms (>= 2000ms, edge-tts network latency)")
            print(f"  - Turn #{m['id']} ('{m['text']}'): {', '.join(reasons)}")
    else:
        print("• 100% of turns met all latency targets.")


if __name__ == "__main__":
    run_speed_audit()
