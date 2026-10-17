"""/ask reports LLM failures honestly; /health never calls the provider; the UI shows the
error instead of presenting graph facts as an answer. Offline: Neo4j reads are faked and the
LLM is either stubbed or the real client over a mock transport."""

import httpx
import pytest
from fastapi.testclient import TestClient

from codeatlas.api import main
from codeatlas.config import Settings
from codeatlas.llm import client as llm
from codeatlas.models import GraphPayload
from codeatlas.qa import answer as qa

from .conftest import EMPTY_ARCHITECTURE, FIXTURE

FAKE_KEY = "sk-test-FAKE9876543210zyxwvuFAKE9876543210zyxwvu"
FACTS = ["File shop/models.py DEFINES Class Cart (lines 3-20)",
         "File shop/cli.py IMPORTS File shop/models.py (line 1)"]


@pytest.fixture
def env(monkeypatch):
    settings = Settings(_env_file=None, neo4j_uri="", gemini_api_key=FAKE_KEY,
                        gemini_base_url="https://llm.example.test/v1beta")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(qa.queries, "project",
                        lambda pid: {"id": pid, "name": "shop", "sync_status": "complete"}
                        if pid == "p1" else None)
    monkeypatch.setattr(qa, "_retrieve", lambda intent, pid, entity: (
        list(FACTS), GraphPayload(), ["CALLS are resolved statically."]))
    monkeypatch.setattr(qa, "resolve_entity", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "ping", lambda: {"ok": True})
    monkeypatch.setattr(llm, "_last_status", {"state": "unknown"})
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    state = {"requests": [], "response": None}

    def handler(request):
        state["requests"].append(request)
        r = state["response"]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(llm, "_transport", httpx.MockTransport(handler))
    state["settings"] = settings
    return state


@pytest.fixture
def client(env):
    return TestClient(main.app)


def ask(client, question="What does this repository do?"):
    return client.post("/ask", json={"project_id": "p1", "question": question})


def ok_completion(text):
    return httpx.Response(200, json={"candidates": [{
        "content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP", "index": 0}]})


def test_successful_answer(env, client):
    env["response"] = ok_completion("**Known (from graph)**: a shop. Cites `shop/models.py`.")
    r = ask(client)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "llm" and body["llm_used"] is True
    assert body["answer"].startswith("**Known") and body["facts"] == FACTS
    # graph facts reached the model; nothing else did
    assert b"shop/models.py DEFINES Class Cart" in env["requests"][0].content


@pytest.mark.parametrize("response,status,code", [
    (httpx.Response(403, headers={"x-request-id": "req_403"},
                    json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                    "message": "forbidden"}}),
     502, "llm_permission_denied"),
    (httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED",
                                         "message": "bad key"}}), 502, "llm_auth_failed"),
    (httpx.Response(429, json={}), 429, "llm_rate_limited"),
    (httpx.Response(500, json={}), 502, "llm_provider_error"),
    (httpx.ReadTimeout("slow"), 504, "llm_timeout"),
    (httpx.ConnectError("refused"), 502, "llm_unreachable"),
    (httpx.Response(200, json={"candidates": []}), 502, "llm_bad_response"),
])
def test_llm_failures_are_not_200(env, client, response, status, code):
    env["response"] = response
    r = ask(client)
    assert r.status_code == status, r.text
    body = r.json()
    detail = body["detail"]
    assert body["component"] == "llm" and detail["code"] == code
    assert detail["hint"] and detail["message"]
    ctx = detail["graph_context"]  # retrieved facts kept, but not presented as an answer
    assert ctx["facts"] == FACTS and ctx["answer"] == "" and ctx["llm_used"] is False
    assert FAKE_KEY not in r.text


def test_403_details_and_no_retry(env, client):
    env["response"] = httpx.Response(403, headers={"x-request-id": "req_403"},
                                     json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                     "message": "forbidden"}})
    detail = ask(client).json()["detail"]
    assert detail["provider_status"] == 403 and detail["request_id"] == "req_403"
    assert "PERMISSION_DENIED" in detail["provider_message"] and len(env["requests"]) == 1


def test_not_configured_is_503(env, client, monkeypatch):
    s = Settings(_env_file=None, neo4j_uri="", gemini_api_key=None)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    r = ask(client)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "llm_not_configured"
    assert env["requests"] == []


def test_clarification_is_an_honest_200_without_llm(env, client):
    r = ask(client, "What depends on the thing?")
    assert r.status_code == 200 and r.json()["kind"] == "clarification"
    assert env["requests"] == []


def test_unknown_project_is_400(env, client):
    r = client.post("/ask", json={"project_id": "nope", "question": "What does it do?"})
    assert r.status_code == 400 and r.json()["component"] == "api"


def test_health_reports_last_call_without_calling_provider(env, client):
    h = client.get("/health").json()
    assert h["llm"]["configured"] is True and h["llm"]["last_call"]["state"] == "unknown"
    assert h["llm"]["base_url_host"] == "llm.example.test"
    assert env["requests"] == []  # health never contacts the LLM
    env["response"] = httpx.Response(403, json={})
    ask(client)
    last = client.get("/health").json()["llm"]["last_call"]
    assert last["state"] == "failed" and last["code"] == "permission_denied"
    assert last["provider_status"] == 403
    env["response"] = ok_completion("fine")
    ask(client)
    assert client.get("/health").json()["llm"]["last_call"]["state"] == "ok"
    assert FAKE_KEY not in client.get("/health").text


# ------------------------------------------------------------------ Streamlit Ask tab

@pytest.fixture
def ui(env, client, monkeypatch):
    from streamlit.testing.v1 import AppTest

    project = {"id": "p1", "name": "shop", "sync_status": "complete", "source_kind": "local"}
    monkeypatch.setattr(main.queries, "list_projects", lambda: [project])

    def request(method, url, timeout=None, **kw):
        path = url.replace(main.get_settings().codeatlas_api_url, "")
        if path.endswith("/architecture"):
            return httpx.Response(200, json=EMPTY_ARCHITECTURE)
        if path.startswith("/graph/"):
            return httpx.Response(200, json={"nodes": [], "edges": [], "cypher": [],
                                             "complete": True, "next_cursor": None})
        if path.endswith("/stats"):
            return httpx.Response(200, json={
                "nodes": {}, "relationships": {}, "call_confidence": [], "import_resolution": [],
                "top_imported_files": [], "top_called_functions": []})
        if path.endswith(("/files", "/functions")):
            return httpx.Response(200, json=[])
        return client.request(method, path, **kw)
    monkeypatch.setattr(httpx, "request", request)
    return AppTest.from_file(str(FIXTURE.parents[2] / "ui" / "app.py"), default_timeout=60)


def _ask_in_ui(at, question):
    at.text_input(key="question").input(question).run()
    return next(b for b in at.button if b.label == "Ask").click().run()


def _all_text(at) -> str:
    parts = [e.value for kind in ("markdown", "error", "info", "caption", "code", "warning")
             for e in getattr(at, kind)]
    return "\n".join(str(p) for p in parts)


def test_ui_shows_llm_failure_not_an_answer(env, ui):
    env["response"] = httpx.Response(403, headers={"x-request-id": "req_ui"},
                                     json={"error": {"message": "forbidden"}})
    at = _ask_in_ui(ui.run(), "What does this repository do?")
    assert not at.exception
    errors = [e.value for e in at.error]
    assert any("No answer: the LLM request failed" in e and "permission_denied" in e
               for e in errors)
    assert any("entitled" in i.value for i in at.info)
    assert any("not an LLM answer" in m.value for m in at.markdown)
    assert not any("answer written by the LLM" in c.value for c in at.caption)
    assert FAKE_KEY not in _all_text(at)
    assert any("🔴 LLM" in m.value for m in at.markdown)  # sidebar reflects the failure


def test_ui_shows_successful_answer(env, ui):
    env["response"] = ok_completion("**Known (from graph)**: it is a shop.")
    at = _ask_in_ui(ui.run(), "What does this repository do?")
    assert not at.exception and not at.error
    assert any("it is a shop" in m.value for m in at.markdown)
    assert any("answer written by the LLM" in c.value for c in at.caption)


def test_ui_sidebar_does_not_claim_unverified_llm_is_healthy(env, ui):
    at = ui.run()
    assert any("configured, not verified yet" in m.value for m in at.markdown)
    assert not any("🟢 LLM" in m.value for m in at.markdown)
