"""Thin client for sending mail through Microsoft Graph."""

import logging

import httpx

from .auth import AuthError, NotSignedIn, build_auth
from .config import Settings
from .models import EmailRequest

log = logging.getLogger(__name__)

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
        # Delegated mode with no usable sign-in: the email should wait, not use up attempts.
        self.not_signed_in = False


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
    def __init__(self, settings: Settings, http: httpx.Client | None = None, auth=None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=settings.graph_timeout_seconds)
        self.auth = auth or build_auth(settings)

    def ready(self) -> bool:
        """False while delegated mode has no sign-in; the worker then leaves the queue alone."""
        return self.auth.ready()

    def _token(self) -> str:
        try:
            return self.auth.get_token()
        except AuthError as exc:
            err = GraphError(str(exc), retryable=exc.retryable)
            err.not_signed_in = isinstance(exc, NotSignedIn)
            raise err from exc

    def send(self, email: EmailRequest) -> None:
        if self.auth.mode == "delegated":
            # Sends as the signed-in account.
            url = f"{self.settings.graph_base_url}/me/sendMail"
        else:
            url = f"{self.settings.graph_base_url}/users/{self.settings.sender_address}/sendMail"
        payload = {"message": build_message(email), "saveToSentItems": self.settings.save_to_sent_items}
        token = self._token()
        try:
            resp = self.http.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
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
