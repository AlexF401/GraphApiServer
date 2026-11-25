import base64

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.graph import GraphMailer
from app.main import app

SEND_URL = "https://graph.microsoft.com/v1.0/users/noreply@example.com/sendMail"


class FakeMsal:
    def __init__(self, result=None):
        self.result = result or {"access_token": "fake-token"}

    def acquire_token_for_client(self, scopes):
        return self.result


@pytest.fixture
def settings():
    return Settings(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        sender_address="noreply@example.com",
        api_keys="billing:key-billing,crm:key-crm",
        max_attachment_bytes=1000,
    )


@pytest.fixture
def client(settings):
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.mailer = GraphMailer(settings, http=httpx.Client(), msal_app=FakeMsal())
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.mailer = None


def payload(**extra):
    body = {"to": ["someone@example.com"], "subject": "Hi", "body": "<p>Hello</p>"}
    body.update(extra)
    return body


def test_api_keys_parse_from_env_string(monkeypatch):
    monkeypatch.setenv("TENANT_ID", "t")
    monkeypatch.setenv("CLIENT_ID", "c")
    monkeypatch.setenv("CLIENT_SECRET", "s")
    monkeypatch.setenv("SENDER_ADDRESS", "a@example.com")
    monkeypatch.setenv("API_KEYS", "billing:k1, crm:k2")
    assert Settings(_env_file=None).api_keys == {"k1": "billing", "k2": "crm"}


def test_rejects_missing_or_bad_key(client):
    assert client.post("/emails", json=payload()).status_code == 401
    assert client.post("/emails", json=payload(), headers={"X-API-Key": "nope"}).status_code == 401


@respx.mock
def test_sends_email_with_attachment(client):
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(202))
    content = base64.b64encode(b"hello file").decode()
    resp = client.post(
        "/emails",
        headers={"X-API-Key": "key-crm"},
        json=payload(
            cc=["cc@example.com"],
            body_type="text",
            attachments=[{"name": "a.txt", "content_type": "text/plain", "content_base64": content}],
        ),
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "sent", "app": "crm"}

    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer fake-token"
    import json

    msg = json.loads(sent.content)["message"]
    assert msg["toRecipients"] == [{"emailAddress": {"address": "someone@example.com"}}]
    assert msg["ccRecipients"] == [{"emailAddress": {"address": "cc@example.com"}}]
    assert msg["body"]["contentType"] == "Text"
    assert msg["attachments"][0]["name"] == "a.txt"
    assert msg["attachments"][0]["contentBytes"] == content


def test_rejects_oversized_attachments(client):
    big = base64.b64encode(b"x" * 1001).decode()
    resp = client.post(
        "/emails",
        headers={"X-API-Key": "key-billing"},
        json=payload(attachments=[{"name": "big.bin", "content_base64": big}]),
    )
    assert resp.status_code == 413


def test_rejects_invalid_base64(client):
    resp = client.post(
        "/emails",
        headers={"X-API-Key": "key-billing"},
        json=payload(attachments=[{"name": "bad", "content_base64": "not base64!!"}]),
    )
    assert resp.status_code == 422


def test_rejects_bad_address(client):
    resp = client.post("/emails", headers={"X-API-Key": "key-billing"}, json=payload(to=["not-an-email"]))
    assert resp.status_code == 422


@respx.mock
def test_graph_error_returns_502(client):
    respx.post(SEND_URL).mock(
        return_value=httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})
    )
    resp = client.post("/emails", headers={"X-API-Key": "key-billing"}, json=payload())
    assert resp.status_code == 502
    assert "Access is denied" in resp.json()["detail"]


def test_token_failure_returns_502(client, settings):
    app.state.mailer = GraphMailer(
        settings, http=httpx.Client(), msal_app=FakeMsal({"error": "invalid_client", "error_description": "bad secret"})
    )
    resp = client.post("/emails", headers={"X-API-Key": "key-billing"}, json=payload())
    assert resp.status_code == 502
    assert "bad secret" in resp.json()["detail"]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
