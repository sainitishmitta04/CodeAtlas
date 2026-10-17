"""Gemini (native Generative Language API) client with typed, redacted failures.

Configuration comes only from Settings (the workspace .env): GEMINI_API_KEY, GEMINI_BASE_URL,
GEMINI_MODEL. The request is `POST {base_url}/models/{model}:generateContent` with the key in
the `x-goog-api-key` header (never in the URL, so it cannot end up in logs).

Every failure becomes an LLMError with a stable `code`, the provider's HTTP status, a short
*redacted* provider message and the provider request id (when sent), so a 403 can be
diagnosed without exposing the credential. Retries are ours, bounded, and only for
transient failures (429, 5xx, timeouts, network); 401/403 are never retried.
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from codeatlas.config import Settings, get_settings

log = logging.getLogger(__name__)

HTTP_STATUS = {  # how the CodeAtlas API reports each failure
    "not_configured": 503, "auth_failed": 502, "permission_denied": 502, "not_found": 502,
    "bad_request": 502, "rate_limited": 429, "timeout": 504, "unreachable": 502,
    "provider_error": 502, "bad_response": 502,
}
RETRYABLE = {"rate_limited", "timeout", "unreachable", "provider_error"}
HINTS = {
    "not_configured": "Set GEMINI_API_KEY (and GEMINI_BASE_URL / GEMINI_MODEL if needed) in the "
                      "workspace .env, then restart the API.",
    "auth_failed": "The provider did not accept the credential (401). Check that the key in "
                   "the workspace .env is current and valid for the Gemini API.",
    "permission_denied": "The provider received the request but refused it (403). The "
                         "configured endpoint, model and key were sent as configured; verify "
                         "with the provider that this key is entitled to this model "
                         "(e.g. API enabled for the project, billing or region access).",
    "not_found": "The provider returned 404: check GEMINI_BASE_URL and that GEMINI_MODEL "
                 "exists for the Gemini API.",
    "bad_request": "The provider rejected the request as invalid; see the provider message.",
    "rate_limited": "The provider's rate limit or quota was reached; wait and retry.",
    "timeout": "The provider did not answer in time; retry, or raise LLM_TIMEOUT_S.",
    "unreachable": "The provider could not be reached (network/DNS/TLS); check connectivity "
                   "and GEMINI_BASE_URL.",
    "provider_error": "The provider reported a server error; retry later.",
    "bad_response": "The provider answered, but not with a usable generated answer.",
}
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class LLMError(RuntimeError):
    def __init__(self, code: str, message: str, *, provider_status: int | None = None,
                 provider_message: str | None = None, request_id: str | None = None,
                 attempts: int = 1):
        super().__init__(message)
        self.code, self.message = code, message
        self.provider_status, self.provider_message = provider_status, provider_message
        self.request_id, self.attempts = request_id, attempts

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 502)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def hint(self) -> str:
        return HINTS.get(self.code, "")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint,
                "provider_status": self.provider_status,
                "provider_message": self.provider_message, "request_id": self.request_id,
                "retryable": self.retryable, "attempts": self.attempts}


LLMUnavailable = LLMError  # backwards-compatible name

# ------------------------------------------------------------------ redaction

_BEARER = re.compile(r"(?i)\b(bearer|token|api[-_ ]?key)\b(\s*[:=]?\s*)\S+")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-.~+=]{32,}")  # keys, JWTs (not URLs)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


def redact(text: str | None, secrets: tuple[str, ...] = (), limit: int = 300) -> str | None:
    """Remove credentials from text meant for logs, API responses or the UI."""
    if text is None:
        return None
    text = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    text = _BEARER.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _LONG_TOKEN.sub("[REDACTED]", text)
    text = re.sub(r"<[^>]{0,200}>", " ", text)  # HTML error pages from gateways/WAFs
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _secrets(settings: Settings) -> tuple[str, ...]:
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    return (key,) if key else ()


# ------------------------------------------------------------------ last observed status

_last_status: dict[str, Any] = {"state": "unknown"}


def _record(state: str, err: LLMError | None = None) -> None:
    global _last_status
    _last_status = {"state": state, "at": datetime.now(timezone.utc).isoformat(),
                    **({"code": err.code, "provider_status": err.provider_status,
                        "request_id": err.request_id} if err else {})}


def status_snapshot() -> dict[str, Any]:
    """Outcome of the most recent real LLM call. 'unknown' until a question is asked; the
    health endpoint never calls the provider itself."""
    return dict(_last_status)


def config_summary(settings: Settings | None = None) -> dict[str, Any]:
    """Non-secret view of the LLM configuration (no key material of any kind)."""
    s = settings or get_settings()
    base = s.gemini_base_url or DEFAULT_BASE_URL
    parts = urlsplit(base)
    return {"configured": s.llm_configured, "model": s.gemini_model,
            "base_url_host": parts.hostname or "generativelanguage.googleapis.com",
            "base_url_valid": parts.scheme in ("http", "https") and bool(parts.hostname)}


# ------------------------------------------------------------------ client

_client: httpx.Client | None = None
_client_key: tuple | None = None
_transport: httpx.BaseTransport | None = None  # tests inject a MockTransport here


def _get_client(settings: Settings) -> httpx.Client:
    """One HTTP client per effective configuration; rebuilt if the configuration changes."""
    global _client, _client_key
    if not settings.llm_configured:
        raise LLMError("not_configured", "LLM is not configured (GEMINI_API_KEY missing).")
    if not config_summary(settings)["base_url_valid"]:
        raise LLMError("not_configured", "GEMINI_BASE_URL is not a valid http(s) URL.")
    secret = settings.gemini_api_key.get_secret_value()
    key = (settings.gemini_base_url, settings.llm_timeout_s,
           hashlib.sha256(secret.encode()).hexdigest())  # hash only, never the key
    if _client is None or _client_key != key:
        if _client is not None:
            _client.close()
        _client = httpx.Client(timeout=settings.llm_timeout_s, transport=_transport,
                               headers={"x-goog-api-key": secret,
                                        "content-type": "application/json"})
        _client_key = key
    return _client


def _request_id(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    for name in ("x-request-id", "request-id", "x-amzn-requestid", "cf-ray"):
        value = response.headers.get(name)
        if value and _SAFE_ID.match(value):
            return value
    return None


def _provider_message(response: httpx.Response, secrets: tuple[str, ...]) -> str | None:
    try:
        body = response.json()
    except ValueError:
        ctype = response.headers.get("content-type", "") or "unknown type"
        return redact(f"non-JSON response ({ctype}): {response.text}", secrets)
    err = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(err, dict):
        parts = [str(err[k]) for k in ("status", "code") if err.get(k) is not None]
        msg = err.get("message") or err.get("detail") or ""
        return redact(" / ".join(parts + [str(msg)]) if parts else str(msg) or str(err),
                      secrets)
    return redact(str(err), secrets)


def _classify_status(response: httpx.Response, secrets: tuple[str, ...]) -> LLMError:
    status = response.status_code
    code = ("auth_failed" if status == 401 else "permission_denied" if status == 403
            else "not_found" if status == 404 else "rate_limited" if status == 429
            else "bad_request" if status in (400, 409, 413, 422)
            else "provider_error")
    return LLMError(code, f"LLM provider returned HTTP {status} ({code.replace('_', ' ')}).",
                    provider_status=status, provider_message=_provider_message(response, secrets),
                    request_id=_request_id(response))


def _retry_delay(attempt: int) -> float:
    return min(0.5 * 2 ** attempt, 4.0)  # bounded exponential backoff


_sleep = time.sleep  # replaced in tests


def chat(system: str, user: str, max_tokens: int = 700,
         settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    secrets = _secrets(settings)
    attempts = 1 + settings.llm_max_retries
    model = settings.gemini_model
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}}
    err: LLMError | None = None
    try:
        client = _get_client(settings)
        url = f"{(settings.gemini_base_url or DEFAULT_BASE_URL).rstrip('/')}/models/{model}:generateContent"
        for attempt in range(attempts):
            try:
                response = client.post(url, json=body)
            except httpx.TimeoutException:
                err = LLMError("timeout", "LLM request timed out.")
            except httpx.TransportError as exc:
                err = LLMError("unreachable", f"Could not reach the LLM provider "
                                              f"({type(exc).__name__}).")
            else:
                if response.status_code >= 400:
                    err = _classify_status(response, secrets)
                else:
                    text = _content(response)  # raises LLMError(bad_response) if unusable
                    _record("ok")
                    return text
            err.attempts = attempt + 1
            if err.retryable and attempt + 1 < attempts:
                _sleep(_retry_delay(attempt))
                continue
            raise err from None
        raise err or LLMError("provider_error", "LLM request failed.")  # pragma: no cover
    except LLMError as exc:
        _record("failed", exc)
        log.warning("LLM call failed: code=%s provider_status=%s request_id=%s attempts=%s "
                    "host=%s model=%s provider_message=%s", exc.code, exc.provider_status,
                    exc.request_id, exc.attempts, config_summary(settings)["base_url_host"],
                    model, redact(exc.provider_message, secrets))
        raise


def _content(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        raise LLMError("bad_response", "LLM provider returned a non-JSON response.",
                       provider_status=response.status_code,
                       request_id=_request_id(response)) from None
    candidates = body.get("candidates") if isinstance(body, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise LLMError("bad_response", "LLM response contained no candidates.",
                       provider_status=response.status_code, request_id=_request_id(response))
    candidate = candidates[0]
    parts = ((candidate.get("content") or {}).get("parts") or []) if isinstance(candidate, dict) else []
    text = "".join(p.get("text", "") for p in parts
                   if isinstance(p, dict) and not p.get("thought")).strip()
    if not text:
        reason = candidate.get("finishReason") if isinstance(candidate, dict) else None
        raise LLMError("bad_response",
                       f"LLM returned an empty answer (finish_reason={reason!s}).",
                       provider_status=response.status_code, request_id=_request_id(response))
    return text
