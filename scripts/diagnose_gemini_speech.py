import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from app.config import GEMINI_API_KEY
from google import genai

client = genai.Client(api_key=GEMINI_API_KEY, http_options={"api_version": "v1beta"})

print("1. Testing unary interactions.create...")
t0 = time.time()
try:
    unary = client.interactions.create(
        model="gemini-3.8-flash-lite-tts",
        input=[{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": "Hello boss",
                "annotations": [{"type": "speech_metadata", "style": "casual"}]
            }]
        }],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": "achird"}]}
    )
    dt = time.time() - t0
    print(f"Unary succeeded in {dt:.2f}s! Output type: {type(unary)}")
except Exception as e:
    print(f"Unary failed in {time.time()-t0:.2f}s: {e}")

print("\n2. Testing streaming interactions.create...")
t0 = time.time()
try:
    stream = client.interactions.create(
        model="gemini-3.8-flash-lite-tts",
        input=[{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": "Hello boss",
                "annotations": [{"type": "speech_metadata", "style": "casual"}]
            }]
        }],
        stream=True,
        response_format={
            "type": "audio",
            "mime_type": "audio/l16",
            "sample_rate": 24000,
        },
        generation_config={"speech_config": [{"voice": "achird"}]}
    )
    chunk_count = 0
    for ev in stream:
        chunk_count += 1
        print(f"Stream event {chunk_count}: {type(ev)} - {dir(ev)}")
        if chunk_count >= 3:
            break
    print(f"Streaming test done in {time.time()-t0:.2f}s")
except Exception as e:
    print(f"Streaming failed in {time.time()-t0:.2f}s: {e}")
