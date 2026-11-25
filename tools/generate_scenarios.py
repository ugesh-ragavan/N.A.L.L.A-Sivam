"""tools/generate_scenarios.py

Scenario Library Factory (Prompt H2).

Generates draft scenario YAML files in data/scenarios/drafts/<category>.yaml
using the configured Gemini model with Persona v3 + Identity Guard v3 rules,
or scaffolds starter batches cleanly for user review in the Scenario Lab.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import config  # noqa: E402
from tools.lint_scenarios import lint_directory  # noqa: E402

DRAFTS_DIR = BASE_DIR / "data" / "scenarios" / "drafts"

STYLE_PROMPT = """Generate {count} offline conversational scenarios for NALLA SIVAM in category '{category}'.
Rules (Identity Guard v3):
1. Short, punchy spoken replies (MAXIMUM 18 words each).
2. At least 4 reply variants per scenario, with tone in [friendly, dry, warm, tanglish].
3. At least 5 trigger phrases total across 'en', 'tanglish', and 'ta' (where natural).
4. NEVER claim eating, sleeping, a body, family, or real feelings. Use playful machine humour or real slots: {requests_today}, {uptime}, {time}, {tasks_open}, {next_task}.
5. NEVER state live/volatile facts (prices, news, weather numbers).
6. Output ONLY valid YAML list matching:
- id: {category}.example_id
  category: {category}
  priority: 10
  triggers:
    en: [...]
    tanglish: [...]
  replies:
    - {{text: "...", tone: friendly}}
"""


def generate_category_with_llm(category: str, count: int = 8) -> Path:
    from app.brain.gemini_provider import GeminiProvider
    provider = GeminiProvider(api_key=config.GEMINI_API_KEY, model=config.GEMINI_CHAT_MODEL)
    prompt = STYLE_PROMPT.format(category=category, count=count)
    resp = provider.generate(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="You output ONLY raw valid YAML lists without markdown fences.",
    )
    raw = resp.text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1])
    data = yaml.safe_load(raw)
    if not isinstance(data, list):
        raise ValueError("Model output did not parse into a YAML list.")
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DRAFTS_DIR / f"{category}.yaml"
    out_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Nalla Sivam draft scenarios.")
    parser.add_argument("--category", required=True, help="Scenario category (e.g. greetings, mood, social)")
    parser.add_argument("--count", type=int, default=8, help="Number of scenarios to generate")
    args = parser.parse_args()

    out = generate_category_with_llm(args.category, args.count)
    print(f"Wrote drafts to {out}")
    rep = lint_directory(DRAFTS_DIR)
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
