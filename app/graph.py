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

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        # True for throttling / transient errors, which the queue retries.
        self.retryable = retryable
        # Seconds Graph asked us to wait (Retry-After header), if it said.
        self.retry_after = retry_after


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
        # Built lazily: MSAL contacts Entra on construction, and the service should
        # still start (and queue mail) if Entra is briefly unreachable.
        self.msal_app = msal_app

    def _msal(self):
        if self.msal_app is None:
            # MSAL caches the token in memory and refreshes it when it expires.
            self.msal_app = msal.ConfidentialClientApplication(
                client_id=self.settings.client_id,
                client_credential=self.settings.client_secret,
                authority=f"https://login.microsoftonline.com/{self.settings.tenant_id}",
            )
        return self.msal_app

    def _token(self) -> str:
        try:
            result = self._msal().acquire_token_for_client(scopes=GRAPH_SCOPE)
        except Exception as exc:  # MSAL raises if Entra is unreachable or the tenant is wrong
            raise GraphError(f"could not get Graph token: {exc}", retryable=True) from exc
        if "access_token" not in result:
            detail = result.get("error_description") or result.get("error") or "unknown error"
            # Bad credentials won't fix themselves, but a network blip reaching Entra will.
            retryable = result.get("error") not in ("invalid_client", "unauthorized_client", "invalid_request")
            raise GraphError(f"could not get Graph token: {detail}", retryable=retryable)
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
        retryable = resp.status_code in (408, 429) or resp.status_code >= 500
        try:
            retry_after = float(resp.headers["Retry-After"])
        except (KeyError, ValueError):
            retry_after = None
        raise GraphError(f"Graph returned {resp.status_code}: {detail}", resp.status_code, retryable, retry_after)
