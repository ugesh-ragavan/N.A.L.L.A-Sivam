"""Test script to verify Google Gemini API connectivity.

Run directly:
    python scripts/test_gemini.py

Do NOT run via pytest — this script calls sys.exit() and is not a
test module. It is excluded by pytest.ini (testpaths = tests).
"""

import os
import sys
from pathlib import Path


def main() -> None:
    """Load .env, build a Gemini client, send a test prompt."""
    root_dir = Path(__file__).resolve().parent.parent

    try:
        from dotenv import load_dotenv
        load_dotenv(root_dir / ".env")
    except ImportError:
        env_file = root_dir / ".env"
        if env_file.exists():
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

    api_key = os.getenv("GEMINI_API_KEY", "")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    if not api_key or api_key in ("your_gemini_api_key_here", ""):
        print("[-] Error: GEMINI_API_KEY is not set in .env")
        print("    Get a free key from https://aistudio.google.com/")
        sys.exit(1)

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents="Say 'Nalla Sivam online, boss!' in one line.",
        )
        print(f"[+] Gemini Response : {response.text.strip()}")
        print(f"[+] Model used      : {model_name}")
        print("[+] Test succeeded: Gemini API connectivity verified.")
    except Exception as exc:
        print(f"[-] Gemini test failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
