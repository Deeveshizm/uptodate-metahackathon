import json

import httpx
import pytest

from backend.generation import Draft, EvidenceSelection, Ollama
from backend.retrieval import SourceError, resolve_public

PEER = "100.100.0.1"


@pytest.mark.parametrize("url,trusted", [
    (f"http://{PEER}:11434", ""),
    (f"http://{PEER}:11434", "100.100.0.2"),
    (f"http://{PEER}:8080", PEER),
    (f"http://{PEER}:11434/v1", PEER),
    (f"http://user:secret@{PEER}:11434", PEER),
    (f"http://{PEER}:11434#fragment", PEER),
    ("http://192.168.1.10:11434", "192.168.1.10"),
    ("http://8.8.8.8:11434", "8.8.8.8"),
    ("http://model.example:11434", "model.example"),
])
def test_remote_model_endpoint_requires_exact_tailnet_opt_in(monkeypatch, url, trusted):
    monkeypatch.setenv("OLLAMA_URL", url)
    monkeypatch.setenv("OLLAMA_TRUSTED_HOST", trusted)
    with pytest.raises(SourceError, match="explicitly trusted"):
        Ollama()


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr("backend.generation.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_remote_client_only_lists_and_infers_without_management(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", f"http://{PEER}:11434")
    monkeypatch.setenv("OLLAMA_TRUSTED_HOST", PEER)
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    monkeypatch.setenv("LOCAL_MODEL_MEMORY_SAVING", "1")
    requests = []
    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert "keep_alive" not in payload
        assert payload["format"]["properties"]["span_ids"]["items"]["enum"] == ["s013"]
        assert "Output JSON schema" in payload["messages"][0]["content"]
        return httpx.Response(200, json={"done": True, "message": {"content": '{"span_ids":["s013"]}'}})
    install_transport(monkeypatch, handler)
    model = Ollama()
    status = model.status()
    assert status["ready"] and not status["local"] and status["transport"] == "tailscale"
    selected = model.complete(EvidenceSelection, "Select evidence", {"spans": [{"id": "s013", "text": "Original text"}]})
    assert selected.span_ids == ["s013"]
    assert requests == [("GET", "/api/tags"), ("POST", "/api/chat")]


def test_writer_schema_limits_citations_to_selected_ids(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")
    def handler(request):
        payload = json.loads(request.content)
        assert payload["format"]["$defs"]["Citation"]["properties"]["span_id"]["enum"] == ["s013", "s016"]
        return httpx.Response(503)
    install_transport(monkeypatch, handler)
    with pytest.raises(SourceError, match="HTTP 503"):
        Ollama().complete(Draft, "Draft", {"spans": [{"id": "s013"}, {"id": "s016"}]})


def test_sleeping_peer_returns_actionable_failure(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", f"http://{PEER}:11434")
    monkeypatch.setenv("OLLAMA_TRUSTED_HOST", PEER)
    def handler(request):
        raise httpx.ConnectError("Peer offline", request=request)
    install_transport(monkeypatch, handler)
    model = Ollama()
    assert not model.status()["ready"]
    with pytest.raises(SourceError, match="MacBook is awake"):
        model.complete(EvidenceSelection, "Select", {"spans": [{"id": "s001"}]})


def test_tailnet_model_allowlist_does_not_allow_article_retrieval(monkeypatch):
    monkeypatch.setenv("OLLAMA_TRUSTED_HOST", PEER)
    monkeypatch.setattr("backend.retrieval.socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (PEER, 80))])
    with pytest.raises(SourceError, match="non-public"):
        resolve_public(PEER, 80)
