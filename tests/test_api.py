import base64
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.graph import GraphError

BILLING = {"X-API-Key": "key-billing"}
CRM = {"X-API-Key": "key-crm"}


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
    assert client.get("/emails", headers={"X-API-Key": "nope"}).status_code == 401


def test_queues_and_worker_sends(client, worker, mailer):
    content = base64.b64encode(b"hello file").decode()
    resp = client.post(
        "/emails",
        headers=CRM,
        json=payload(attachments=[{"name": "a.txt", "content_type": "text/plain", "content_base64": content}]),
    )
    assert resp.status_code == 202
    email_id = resp.json()["id"]
    assert resp.json()["status"] == "queued"
    assert mailer.sent == []

    assert worker.run_once() is True
    assert len(mailer.sent) == 1
    assert mailer.sent[0].attachments[0].name == "a.txt"

    status = client.get(f"/emails/{email_id}", headers=CRM).json()
    assert status["status"] == "sent"
    assert status["attempts"] == 1
    assert status["sent_at"] is not None


def test_scheduled_email_waits_until_send_at(client, worker, mailer):
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    resp = client.post("/emails", headers=BILLING, json=payload(send_at=later))
    assert resp.status_code == 202
    assert worker.run_once() is False
    assert mailer.sent == []


def test_naive_send_at_is_utc(client):
    resp = client.post("/emails", headers=BILLING, json=payload(send_at="2030-01-01T09:00:00"))
    assert resp.json()["send_at"].startswith("2030-01-01T09:00:00")
    assert resp.json()["send_at"].endswith(("Z", "+00:00"))


def test_failure_log_and_retry_endpoint(client, worker, mailer):
    email_id = client.post("/emails", headers=BILLING, json=payload()).json()["id"]
    mailer.errors.append(GraphError("Graph returned 403: Access is denied.", 403, retryable=False))
    worker.run_once()

    failed = client.get("/emails?status=failed", headers=BILLING).json()
    assert [e["id"] for e in failed] == [email_id]
    assert "Access is denied" in failed[0]["last_error"]

    resp = client.post(f"/emails/{email_id}/retry", headers=BILLING)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    worker.run_once()
    assert client.get(f"/emails/{email_id}", headers=BILLING).json()["status"] == "sent"

    # A sent email can't be retried.
    assert client.post(f"/emails/{email_id}/retry", headers=BILLING).status_code == 409


def test_apps_only_see_their_own_emails(client):
    email_id = client.post("/emails", headers=BILLING, json=payload()).json()["id"]
    assert client.get(f"/emails/{email_id}", headers=CRM).status_code == 404
    assert client.get("/emails", headers=CRM).json() == []
    assert len(client.get("/emails", headers=BILLING).json()) == 1


def test_rejects_oversized_attachments(client):
    big = base64.b64encode(b"x" * 1001).decode()
    resp = client.post("/emails", headers=BILLING, json=payload(attachments=[{"name": "big.bin", "content_base64": big}]))
    assert resp.status_code == 413


def test_rejects_invalid_base64(client):
    resp = client.post(
        "/emails", headers=BILLING, json=payload(attachments=[{"name": "bad", "content_base64": "not base64!!"}])
    )
    assert resp.status_code == 422


def test_rejects_bad_address(client):
    assert client.post("/emails", headers=BILLING, json=payload(to=["not-an-email"])).status_code == 422


def test_health_reports_queue(client):
    client.post("/emails", headers=BILLING, json=payload())
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["queue"]["queued"] == 1
