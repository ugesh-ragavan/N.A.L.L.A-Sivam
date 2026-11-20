"""Test script to verify Google Gemini API connectivity."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env
root_dir = Path(__file__).resolve().parent.parent
load_dotenv(root_dir / ".env")

api_key = os.getenv("GEMINI_API_KEY")
model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not api_key or api_key == "your_gemini_api_key_here":
    print("[-] Error: GEMINI_API_KEY is not set in .env")
    print("    Please get a free key from https://aistudio.google.com/ and add it to .env")
    sys.exit(1)

try:
    from google import genai
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents="Say 'Nalla Sivam online, boss!' in one line."
    )
    print(f"[+] Gemini Response: {response.text.strip()}")
    print("[+] Test succeeded: Gemini API connectivity verified.")
except Exception as e:
    print(f"[-] Gemini test failed: {e}")
    sys.exit(1)
