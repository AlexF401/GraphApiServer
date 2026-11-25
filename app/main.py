import hmac
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from .config import Settings, get_settings
from .graph import GraphError, GraphMailer
from .models import EmailAccepted, EmailRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("graphapiserver")

app = FastAPI(title="Graph email service", version="0.1.0")


def get_mailer(request: Request) -> GraphMailer:
    mailer = getattr(request.app.state, "mailer", None)
    if mailer is None:
        mailer = request.app.state.mailer = GraphMailer(get_settings())
    return mailer


def require_app(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> str:
    """Return the calling app's name for a valid X-API-Key header, else 401."""
    if x_api_key:
        for key, name in settings.api_keys.items():
            if hmac.compare_digest(key.encode(), x_api_key.encode()):
                return name
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid X-API-Key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/emails", status_code=status.HTTP_202_ACCEPTED, response_model=EmailAccepted)
def send_email(
    email: EmailRequest,
    app_name: str = Depends(require_app),
    settings: Settings = Depends(get_settings),
    mailer: GraphMailer = Depends(get_mailer),
) -> EmailAccepted:
    if email.total_attachment_bytes > settings.max_attachment_bytes:
        raise HTTPException(
            413,
            f"attachments total {email.total_attachment_bytes} bytes; limit is {settings.max_attachment_bytes}",
        )
    try:
        mailer.send(email)
    except GraphError as exc:
        log.error("send failed app=%s to=%s subject=%r: %s", app_name, email.to, email.subject, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    log.info("sent app=%s to=%s subject=%r attachments=%d", app_name, email.to, email.subject, len(email.attachments))
    return EmailAccepted(status="sent", app=app_name)
