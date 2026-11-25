"""Thin client for sending mail through Microsoft Graph with app-only auth."""

import logging

import httpx
import msal

from .config import Settings
from .models import EmailRequest

log = logging.getLogger(__name__)

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


class GraphError(Exception):
    """Raised when Graph (or token acquisition) rejects a send."""

    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        # True for throttling / transient server errors; the queue in step 2 uses this.
        self.retryable = retryable


def build_message(email: EmailRequest) -> dict:
    def recipients(addresses):
        return [{"emailAddress": {"address": str(a)}} for a in addresses]

    message: dict = {
        "subject": email.subject,
        "body": {"contentType": "HTML" if email.body_type == "html" else "Text", "content": email.body},
        "toRecipients": recipients(email.to),
    }
    if email.cc:
        message["ccRecipients"] = recipients(email.cc)
    if email.bcc:
        message["bccRecipients"] = recipients(email.bcc)
    if email.reply_to:
        message["replyTo"] = recipients(email.reply_to)
    if email.attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": a.name,
                "contentType": a.content_type,
                "contentBytes": a.content_base64,
            }
            for a in email.attachments
        ]
    return message


class GraphMailer:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, msal_app=None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=settings.graph_timeout_seconds)
        # MSAL caches the token in memory and refreshes it when it expires.
        self.msal_app = msal_app or msal.ConfidentialClientApplication(
            client_id=settings.client_id,
            client_credential=settings.client_secret,
            authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
        )

    def _token(self) -> str:
        result = self.msal_app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        if "access_token" not in result:
            detail = result.get("error_description") or result.get("error") or "unknown error"
            raise GraphError(f"could not get Graph token: {detail}")
        return result["access_token"]

    def send(self, email: EmailRequest) -> None:
        url = f"{self.settings.graph_base_url}/users/{self.settings.sender_address}/sendMail"
        payload = {"message": build_message(email), "saveToSentItems": self.settings.save_to_sent_items}
        try:
            resp = self.http.post(url, json=payload, headers={"Authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as exc:
            raise GraphError(f"could not reach Graph: {exc}", retryable=True) from exc

        if resp.status_code == 202:
            return

        try:
            detail = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            detail = resp.text
        retryable = resp.status_code == 429 or resp.status_code >= 500
        raise GraphError(f"Graph returned {resp.status_code}: {detail}", resp.status_code, retryable)
