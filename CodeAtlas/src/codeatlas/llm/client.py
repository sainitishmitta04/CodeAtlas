"""Minimal OpenAI-compatible chat client. Failures raise LLMUnavailable, never crash callers."""

import logging

from openai import OpenAI, OpenAIError

from codeatlas.config import get_settings

log = logging.getLogger(__name__)
_client: OpenAI | None = None


class LLMUnavailable(RuntimeError):
    pass


def _get_client() -> OpenAI:
    global _client
    s = get_settings()
    if not s.llm_configured:
        raise LLMUnavailable("LLM is not configured (OPENAI_API_KEY missing).")
    if _client is None:
        _client = OpenAI(api_key=s.openai_api_key.get_secret_value(),
                         base_url=s.openai_base_url or None,
                         timeout=s.llm_timeout_s, max_retries=1)
    return _client


def chat(system: str, user: str, max_tokens: int = 700) -> str:
    try:
        resp = _get_client().chat.completions.create(
            model=get_settings().openai_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
    except OpenAIError as exc:
        log.warning("LLM call failed: %s", type(exc).__name__)
        raise LLMUnavailable(f"LLM request failed ({type(exc).__name__}).") from exc
    content = resp.choices[0].message.content if resp.choices else None
    if not content:
        raise LLMUnavailable("LLM returned an empty response.")
    return content.strip()
