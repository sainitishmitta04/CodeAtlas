"""Standalone check that the configured Gemini endpoint in the workspace .env answers.

Run from CodeAtlas/ with the workspace venv:
    ../.venv/bin/python tests/check_llm_connection.py

Uses the app's own client (codeatlas.llm.client.chat) and Settings. Secrets are never printed.
"""

import sys

from codeatlas.config import get_settings
from codeatlas.llm import client as llm


def main() -> int:
    s = get_settings()
    summary = llm.config_summary(s)
    print(f"Endpoint host : {summary['base_url_host']}")
    print(f"Model         : {summary['model']}")
    if not s.llm_configured:
        print("FAIL: GEMINI_API_KEY is missing or empty in the root .env")
        return 1
    try:
        reply = llm.chat("Answer briefly.", "Reply with the single word: pong",
                         max_tokens=200, settings=s)
    except llm.LLMError as exc:
        print(f"FAIL: {exc.code} - {exc.message}")
        if exc.provider_message:
            print(f"      provider: {exc.provider_message}")
        print(f"      hint: {exc.hint}")
        return 2
    print(f"OK: connected. Model replied: {reply!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
