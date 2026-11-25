"""Background worker that drains the email queue through Graph."""

import logging
import threading
import time
from datetime import timedelta

from .config import Settings
from .graph import GraphError
from .models import EmailRequest
from .store import EmailRecord, EmailStore, utcnow

log = logging.getLogger("graphapiserver.worker")


class QueueWorker:
    def __init__(self, store: EmailStore, mailer, settings: Settings):
        self.store = store
        self.mailer = mailer
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signin_warning = 0.0

    def backoff_seconds(self, attempts: int, retry_after: float | None) -> float:
        delay = min(self.settings.retry_base_seconds * 2 ** (attempts - 1), self.settings.retry_max_seconds)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay

    def process(self, record: EmailRecord) -> None:
        """Try to send one claimed email and record the outcome."""
        try:
            self.mailer.send(EmailRequest.model_validate(record.payload))
        except GraphError as exc:
            if exc.not_signed_in:
                # Leave it queued; the worker pauses until someone signs in.
                self.store.release(record.id, str(exc))
                log.warning("not signed in, email id=%s stays queued: %s", record.id, exc)
            else:
                self._handle_failure(record, str(exc), exc.retryable, exc.retry_after)
        except Exception as exc:  # never let one bad email kill the worker
            log.exception("unexpected error sending id=%s", record.id)
            self._handle_failure(record, f"unexpected error: {exc!r}", retryable=False, retry_after=None)
        else:
            self.store.mark_sent(record.id)
            log.info(
                "sent id=%s app=%s to=%s subject=%r attempt=%d",
                record.id, record.app, record.recipients, record.subject, record.attempts,
            )

    def _handle_failure(self, record: EmailRecord, error: str, retryable: bool, retry_after: float | None) -> None:
        if retryable and record.attempts < self.settings.max_attempts:
            delay = self.backoff_seconds(record.attempts, retry_after)
            self.store.mark_retry(record.id, error, utcnow() + timedelta(seconds=delay))
            log.warning(
                "send failed, will retry id=%s app=%s attempt=%d/%d in %.0fs: %s",
                record.id, record.app, record.attempts, self.settings.max_attempts, delay, error,
            )
        else:
            self.store.mark_failed(record.id, error)
            log.error(
                "send FAILED id=%s app=%s to=%s subject=%r attempts=%d: %s",
                record.id, record.app, record.recipients, record.subject, record.attempts, error,
            )

    def run_once(self) -> bool:
        """Send at most one due email. Returns True if one was processed."""
        if not self.mailer.ready():
            # Delegated mode with nobody signed in: keep emails queued rather than failing them.
            if self.store.counts()["queued"] and time.monotonic() - self._last_signin_warning > 300:
                log.warning("not signed in to Microsoft: queued emails are waiting. Run the sign-in command.")
                self._last_signin_warning = time.monotonic()
            return False
        record = self.store.claim_due()
        if record is None:
            return False
        self.process(record)
        return True

    def _loop(self) -> None:
        log.info("queue worker started")
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                log.exception("queue worker error")
                worked = False
            # Pace sends during a burst; poll slowly when idle.
            wait = self.settings.send_interval_seconds if worked else self.settings.worker_poll_seconds
            self._stop.wait(wait)
        log.info("queue worker stopped")

    def start(self) -> None:
        recovered = self.store.recover_interrupted()
        if recovered:
            log.warning("re-queued %d email(s) interrupted by a restart", recovered)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="email-queue-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
