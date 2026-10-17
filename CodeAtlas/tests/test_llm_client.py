"""LLM client: configuration from the .env, typed redacted errors, bounded retries (offline).

The Gemini client talks to the Generative Language API over httpx; an httpx.MockTransport
stands in for the network, so the error classification is exercised against genuine HTTP
responses. Only fake credentials.
"""

import logging

import httpx
import pytest

from codeatlas.config import Settings, shadowed_env_names
from codeatlas.llm import client as llm

FAKE_KEY = "AIza-test-FAKE0123456789abcdefFAKE0123456789abcdef"
FAKE_BASE = "https://llm.example.test/api/v1beta"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(f"GEMINI_API_KEY={FAKE_KEY}\nGEMINI_BASE_URL={FAKE_BASE}\n"
                   "GEMINI_MODEL=gemini-3.8-flash\nNEO4J_URI=\n")
    return Settings(_env_file=env)


@pytest.fixture
def transport(monkeypatch):
    """Route the client through a scripted transport; record every request."""
    state = {"responses": [], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        item = state["responses"].pop(0) if len(state["responses"]) > 1 else state["responses"][0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    monkeypatch.setattr(llm, "_last_status", {"state": "unknown"})
    return state


def completion(text="Known (from graph): x", finish="STOP"):
    parts = [{"text": text}] if text is not None else []
    return httpx.Response(200, json={"candidates": [{
        "content": {"role": "model", "parts": parts}, "finishReason": finish, "index": 0}]})


# ------------------------------------------------------------------ configuration

def test_env_file_is_the_source_of_truth(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"GEMINI_API_KEY={FAKE_KEY}\nGEMINI_BASE_URL={FAKE_BASE}\n"
                   "GEMINI_MODEL=model-from-env-file\n")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-stale-shell-value")
    monkeypatch.setenv("GEMINI_MODEL", "stale-shell-model")
    s = Settings(_env_file=env)
    assert s.gemini_api_key.get_secret_value() == FAKE_KEY
    assert s.gemini_base_url == FAKE_BASE and s.gemini_model == "model-from-env-file"
    assert shadowed_env_names(env) == ["GEMINI_API_KEY", "GEMINI_MODEL"]  # names only


def test_shell_only_fills_gaps(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"GEMINI_API_KEY={FAKE_KEY}\n")
    monkeypatch.setenv("GEMINI_MODEL", "shell-model")
    assert Settings(_env_file=env).gemini_model == "shell-model"
    assert shadowed_env_names(env) == []


def test_request_uses_configured_endpoint_key_and_model(settings, transport):
    transport["responses"] = [completion("hello")]
    assert llm.chat("sys", "user", settings=settings) == "hello"
    req = transport["requests"][0]
    assert str(req.url) == FAKE_BASE + "/models/gemini-3.8-flash:generateContent"
    assert req.headers["x-goog-api-key"] == FAKE_KEY
    assert "authorization" not in req.headers
    assert FAKE_KEY not in str(req.url)  # key travels in a header, never in the URL
    assert b'"systemInstruction"' in req.content and b'"maxOutputTokens"' in req.content
    assert llm.status_snapshot()["state"] == "ok"


def test_config_summary_has_no_key_material(settings):
    summary = llm.config_summary(settings)
    assert summary == {"configured": True, "model": "gemini-3.8-flash",
                       "base_url_host": "llm.example.test", "base_url_valid": True}
    assert FAKE_KEY not in repr(summary)


def test_default_base_url_when_unset(transport):
    s = Settings(_env_file=None, gemini_api_key=FAKE_KEY, gemini_base_url=None)
    transport["responses"] = [completion()]
    llm.chat("s", "u", settings=s)
    assert str(transport["requests"][0].url).startswith(
        "https://generativelanguage.googleapis.com/v1beta/models/")


def test_not_configured_makes_no_request(transport):
    s = Settings(_env_file=None, gemini_api_key=None)
    with pytest.raises(llm.LLMError) as e:
        llm.chat("s", "u", settings=s)
    assert e.value.code == "not_configured" and e.value.http_status == 503
    assert transport["requests"] == []


def test_invalid_base_url_is_reported(transport):
    s = Settings(_env_file=None, gemini_api_key=FAKE_KEY, gemini_base_url="generativelanguage/v1")
    with pytest.raises(llm.LLMError, match="valid http"):
        llm.chat("s", "u", settings=s)
    assert transport["requests"] == []


def test_client_rebuilt_when_configuration_changes(settings, transport):
    transport["responses"] = [completion()]
    llm.chat("s", "u", settings=settings)
    other = Settings(_env_file=None, gemini_api_key="AIza-other-FAKE-key-000000000000",
                     gemini_base_url=FAKE_BASE)
    llm.chat("s", "u", settings=other)
    keys = [r.headers["x-goog-api-key"] for r in transport["requests"]]
    assert keys == [FAKE_KEY, "AIza-other-FAKE-key-000000000000"]


# ------------------------------------------------------------------ provider failures

def _fail(settings, transport, *responses) -> llm.LLMError:
    transport["responses"] = list(responses)
    with pytest.raises(llm.LLMError) as e:
        llm.chat("s", "u", settings=settings)
    return e.value


def test_403_is_permission_denied_with_redacted_details(settings, transport, caplog):
    caplog.set_level(logging.DEBUG)
    err = _fail(settings, transport, httpx.Response(
        403, headers={"x-request-id": "req_abc123", "x-should-retry": "true"},
        json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                        "message": f"Key {FAKE_KEY} is not allowed to use this model"}}))
    assert err.code == "permission_denied" and err.http_status == 502
    assert err.provider_status == 403 and err.request_id == "req_abc123"
    assert "PERMISSION_DENIED" in err.provider_message
    assert "entitled" in err.hint and not err.retryable
    assert len(transport["requests"]) == 1  # never retried, even with x-should-retry
    assert FAKE_KEY not in repr(err.to_dict()) and FAKE_KEY not in caplog.text
    assert "code=permission_denied" in caplog.text and "req_abc123" in caplog.text
    snap = llm.status_snapshot()
    assert snap["state"] == "failed" and snap["code"] == "permission_denied"


def test_403_html_gateway_page(settings, transport):
    err = _fail(settings, transport, httpx.Response(
        403, headers={"content-type": "text/html", "cf-ray": "8a1b2c3d4e-LHR"},
        text="<html><body><h1>403 Forbidden</h1>Access denied</body></html>"))
    assert err.code == "permission_denied" and err.request_id == "8a1b2c3d4e-LHR"
    assert "non-JSON response (text/html)" in err.provider_message
    assert "403 Forbidden" in err.provider_message and "<h1>" not in err.provider_message


@pytest.mark.parametrize("status,code,http_status,retried", [
    (401, "auth_failed", 502, False),
    (404, "not_found", 502, False),
    (400, "bad_request", 502, False),
    (429, "rate_limited", 429, True),
    (500, "provider_error", 502, True),
    (503, "provider_error", 502, True),
])
def test_status_errors(settings, transport, status, code, http_status, retried):
    err = _fail(settings, transport, httpx.Response(status, json={"error": {"message": "nope"}}))
    assert (err.code, err.http_status, err.provider_status) == (code, http_status, status)
    assert len(transport["requests"]) == (2 if retried else 1)  # bounded: 1 retry at most
    assert err.attempts == len(transport["requests"])


def test_transient_failure_then_success(settings, transport):
    transport["responses"] = [httpx.Response(502, json={}), completion("recovered")]
    assert llm.chat("s", "u", settings=settings) == "recovered"
    assert len(transport["requests"]) == 2


def test_retries_can_be_disabled(settings, transport):
    s = settings.model_copy(update={"llm_max_retries": 0})
    _fail(s, transport, httpx.Response(429, json={}))
    assert len(transport["requests"]) == 1


def test_timeout(settings, transport):
    err = _fail(settings, transport, httpx.ReadTimeout("slow"))
    assert err.code == "timeout" and err.http_status == 504 and len(transport["requests"]) == 2


def test_connection_failure(settings, transport):
    err = _fail(settings, transport, httpx.ConnectError("dns failure"))
    assert err.code == "unreachable" and err.http_status == 502 and err.retryable


@pytest.mark.parametrize("response,fragment", [
    (completion(text=""), "empty answer (finish_reason=STOP)"),
    (completion(text=None, finish="SAFETY"), "finish_reason=SAFETY"),
    (httpx.Response(200, json={"candidates": []}), "no candidates"),
    (httpx.Response(200, json={"unexpected": True}), "no candidates"),
])
def test_malformed_responses(settings, transport, response, fragment):
    err = _fail(settings, transport, response)
    assert err.code == "bad_response" and fragment in err.message
    assert len(transport["requests"]) == 1


def test_thought_parts_are_not_the_answer(settings, transport):
    transport["responses"] = [httpx.Response(200, json={"candidates": [{
        "content": {"parts": [{"text": "internal reasoning", "thought": True},
                              {"text": "final answer"}]}, "finishReason": "STOP"}]})]
    assert llm.chat("s", "u", settings=settings) == "final answer"


def test_no_secret_in_any_failure(settings, transport, caplog):
    caplog.set_level(logging.DEBUG)
    for resp in (httpx.Response(401, json={"error": {"message": f"Bearer {FAKE_KEY} invalid"}}),
                 httpx.Response(403, text=f"denied for {FAKE_KEY}"),
                 httpx.Response(500, json={"error": {"message": f"key={FAKE_KEY}"}})):
        err = _fail(settings, transport, resp)
        assert FAKE_KEY not in repr(err.to_dict()) and FAKE_KEY not in str(err)
    assert FAKE_KEY not in caplog.text


@pytest.mark.parametrize("text,leaked", [
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("api_key=sk-12345678", "sk-12345678"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig", "eyJhbGci"),
    ("x " + "A" * 40, "A" * 40),
])
def test_redact(text, leaked):
    assert leaked not in llm.redact(text)


def test_redact_keeps_urls_readable():
    assert "llm.example.test/api/v1beta/models" in llm.redact(
        "POST https://llm.example.test/api/v1beta/models")
