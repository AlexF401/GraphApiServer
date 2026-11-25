import hmac
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status

from .config import Settings, get_settings
from .graph import GraphMailer
from .models import EmailQueued, EmailRequest, EmailStatus
from .store import EmailStore
from .worker import QueueWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("graphapiserver")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests may pre-populate app.state with fakes; only build what's missing.
    settings = get_settings()
    if getattr(app.state, "store", None) is None:
        app.state.store = EmailStore(settings.database_path)
    if getattr(app.state, "mailer", None) is None:
        app.state.mailer = GraphMailer(settings)
    worker = None
    if settings.run_worker:
        worker = QueueWorker(app.state.store, app.state.mailer, settings)
        worker.start()
    try:
        yield
    finally:
        if worker:
            worker.stop()


app = FastAPI(title="Graph email service", version="0.2.0", lifespan=lifespan)


def get_store(request: Request) -> EmailStore:
    return request.app.state.store


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


def owned_email(email_id: str, app_name: str, store: EmailStore):
    record = store.get(email_id)
    # Apps only see their own emails; report someone else's as not found.
    if record is None or record.app != app_name:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    return record


@app.get("/health")
def health(store: EmailStore = Depends(get_store)) -> dict:
    return {"status": "ok", "queue": store.counts()}


@app.post("/emails", status_code=status.HTTP_202_ACCEPTED, response_model=EmailQueued)
def queue_email(
    email: EmailRequest,
    app_name: str = Depends(require_app),
    settings: Settings = Depends(get_settings),
    store: EmailStore = Depends(get_store),
) -> EmailQueued:
    if email.total_attachment_bytes > settings.max_attachment_bytes:
        raise HTTPException(
            413,
            f"attachments total {email.total_attachment_bytes} bytes; limit is {settings.max_attachment_bytes}",
        )
    payload = email.model_dump(mode="json", exclude={"send_at"})
    record = store.enqueue(app_name, payload, email.send_at)
    log.info(
        "queued id=%s app=%s to=%s subject=%r send_at=%s attachments=%d",
        record.id, app_name, record.recipients, email.subject, record.send_at.isoformat(), len(email.attachments),
    )
    return EmailQueued(id=record.id, status=record.status, send_at=record.send_at)


@app.get("/emails", response_model=list[EmailStatus])
def list_emails(
    status_filter: Annotated[Literal["queued", "sending", "sent", "failed"] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    app_name: str = Depends(require_app),
    store: EmailStore = Depends(get_store),
) -> list[EmailStatus]:
    """This app's emails, newest first. Use ?status=failed for the failure log."""
    return [EmailStatus.from_record(r) for r in store.list(app_name, status_filter, limit, offset)]


@app.get("/emails/{email_id}", response_model=EmailStatus)
def get_email(email_id: str, app_name: str = Depends(require_app), store: EmailStore = Depends(get_store)) -> EmailStatus:
    return EmailStatus.from_record(owned_email(email_id, app_name, store))


@app.post("/emails/{email_id}/retry", response_model=EmailStatus)
def retry_email(email_id: str, app_name: str = Depends(require_app), store: EmailStore = Depends(get_store)) -> EmailStatus:
    owned_email(email_id, app_name, store)
    if not store.requeue_failed(email_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "only failed emails can be retried")
    log.info("re-queued failed email id=%s app=%s", email_id, app_name)
    return EmailStatus.from_record(store.get(email_id))
