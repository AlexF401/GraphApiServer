import base64
import json

import httpx
import pytest
import respx

from app.auth import AppOnlyAuth
from app.graph import GraphError, GraphMailer
from app.models import EmailRequest

from .conftest import FakeMsal

SEND_URL = "https://graph.microsoft.com/v1.0/users/noreply@example.com/sendMail"


def email(**extra):
    body = {"to": ["someone@example.com"], "subject": "Hi", "body": "<p>Hello</p>"}
    body.update(extra)
    return EmailRequest.model_validate(body)


@respx.mock
def test_builds_graph_payload(settings):
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(202))
    content = base64.b64encode(b"hello file").decode()
    GraphMailer(settings, http=httpx.Client(), auth=AppOnlyAuth(settings, msal_app=FakeMsal())).send(
        email(
            cc=["cc@example.com"],
            body_type="text",
            attachments=[{"name": "a.txt", "content_type": "text/plain", "content_base64": content}],
        )
    )
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer fake-token"
    body = json.loads(sent.content)
    msg = body["message"]
    assert body["saveToSentItems"] is False
    assert msg["toRecipients"] == [{"emailAddress": {"address": "someone@example.com"}}]
    assert msg["ccRecipients"] == [{"emailAddress": {"address": "cc@example.com"}}]
    assert msg["body"]["contentType"] == "Text"
    assert msg["attachments"][0] == {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "a.txt",
        "contentType": "text/plain",
        "contentBytes": content,
    }


@respx.mock
def test_403_is_not_retryable(settings):
    respx.post(SEND_URL).mock(return_value=httpx.Response(403, json={"error": {"message": "Access is denied."}}))
    with pytest.raises(GraphError) as exc:
        GraphMailer(settings, http=httpx.Client(), auth=AppOnlyAuth(settings, msal_app=FakeMsal())).send(email())
    assert exc.value.status_code == 403
    assert not exc.value.retryable
    assert "Access is denied" in str(exc.value)


@respx.mock
def test_429_is_retryable_with_retry_after(settings):
    respx.post(SEND_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "42"}, json={}))
    with pytest.raises(GraphError) as exc:
        GraphMailer(settings, http=httpx.Client(), auth=AppOnlyAuth(settings, msal_app=FakeMsal())).send(email())
    assert exc.value.retryable
    assert exc.value.retry_after == 42


@respx.mock
def test_network_error_is_retryable(settings):
    respx.post(SEND_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(GraphError) as exc:
        GraphMailer(settings, http=httpx.Client(), auth=AppOnlyAuth(settings, msal_app=FakeMsal())).send(email())
    assert exc.value.retryable


def test_bad_credentials_not_retryable(settings):
    msal = FakeMsal({"error": "invalid_client", "error_description": "bad secret"})
    with pytest.raises(GraphError) as exc:
        GraphMailer(settings, http=httpx.Client(), auth=AppOnlyAuth(settings, msal_app=msal)).send(email())
    assert not exc.value.retryable
    assert "bad secret" in str(exc.value)
