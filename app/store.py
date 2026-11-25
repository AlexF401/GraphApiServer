"""SQLite-backed email queue.

One database file per server. Every queued email, its attempts and the last
error are kept here, so the failure log survives restarts.
"""

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

QUEUED, SENDING, SENT, FAILED = "queued", "sending", "sent", "failed"
STATUSES = (QUEUED, SENDING, SENT, FAILED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id               TEXT PRIMARY KEY,
    app              TEXT NOT NULL,
    status           TEXT NOT NULL,
    payload          TEXT NOT NULL,
    subject          TEXT NOT NULL,
    recipients       TEXT NOT NULL,
    send_at          TEXT NOT NULL,
    next_attempt_at  TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    sent_at          TEXT
);
CREATE INDEX IF NOT EXISTS emails_due ON emails (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS emails_app_status ON emails (app, status, created_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db(dt: datetime) -> str:
    # Fixed-width UTC ISO strings sort correctly as text.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_db(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@dataclass
class EmailRecord:
    id: str
    app: str
    status: str
    payload: dict
    subject: str
    recipients: list[str]
    send_at: datetime
    next_attempt_at: datetime
    attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EmailRecord":
        return cls(
            id=row["id"],
            app=row["app"],
            status=row["status"],
            payload=json.loads(row["payload"]),
            subject=row["subject"],
            recipients=json.loads(row["recipients"]),
            send_at=from_db(row["send_at"]),
            next_attempt_at=from_db(row["next_attempt_at"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
            created_at=from_db(row["created_at"]),
            updated_at=from_db(row["updated_at"]),
            sent_at=from_db(row["sent_at"]),
        )


class EmailStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue(self, app: str, payload: dict, send_at: datetime | None = None) -> EmailRecord:
        now = utcnow()
        send_at = send_at or now
        recipients = list(payload.get("to", [])) + list(payload.get("cc", [])) + list(payload.get("bcc", []))
        email_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO emails (id, app, status, payload, subject, recipients, send_at, next_attempt_at,"
                " attempts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    email_id,
                    app,
                    QUEUED,
                    json.dumps(payload),
                    payload.get("subject", ""),
                    json.dumps(recipients),
                    to_db(send_at),
                    to_db(send_at),
                    to_db(now),
                    to_db(now),
                ),
            )
        return self.get(email_id)

    def get(self, email_id: str) -> EmailRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        return EmailRecord.from_row(row) if row else None

    def list(self, app: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> list[EmailRecord]:
        sql, args = "SELECT * FROM emails WHERE 1=1", []
        if app is not None:
            sql += " AND app = ?"
            args.append(app)
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [EmailRecord.from_row(r) for r in rows]

    def claim_due(self, now: datetime | None = None) -> EmailRecord | None:
        """Atomically take the oldest due email and mark it as sending."""
        now = now or utcnow()
        with self._lock:
            row = self._conn.execute(
                "UPDATE emails SET status = ?, attempts = attempts + 1, updated_at = ?"
                " WHERE id = (SELECT id FROM emails WHERE status = ? AND next_attempt_at <= ?"
                "             ORDER BY next_attempt_at, created_at LIMIT 1)"
                " RETURNING *",
                (SENDING, to_db(now), QUEUED, to_db(now)),
            ).fetchone()
        return EmailRecord.from_row(row) if row else None

    def mark_sent(self, email_id: str) -> None:
        now = to_db(utcnow())
        with self._lock:
            self._conn.execute(
                "UPDATE emails SET status = ?, sent_at = ?, updated_at = ?, last_error = NULL WHERE id = ?",
                (SENT, now, now, email_id),
            )

    def mark_retry(self, email_id: str, error: str, next_attempt_at: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE emails SET status = ?, last_error = ?, next_attempt_at = ?, updated_at = ? WHERE id = ?",
                (QUEUED, error, to_db(next_attempt_at), to_db(utcnow()), email_id),
            )

    def release(self, email_id: str, error: str) -> None:
        """Put a claimed email back without counting the attempt (e.g. nobody is signed in)."""
        now = to_db(utcnow())
        with self._lock:
            self._conn.execute(
                "UPDATE emails SET status = ?, attempts = MAX(attempts - 1, 0), last_error = ?,"
                " next_attempt_at = ?, updated_at = ? WHERE id = ?",
                (QUEUED, error, now, now, email_id),
            )

    def mark_failed(self, email_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE emails SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (FAILED, error, to_db(utcnow()), email_id),
            )

    def requeue_failed(self, email_id: str) -> bool:
        """Put a failed email back in the queue for immediate sending, with a fresh attempt count."""
        now = to_db(utcnow())
        with self._lock:
            cur = self._conn.execute(
                "UPDATE emails SET status = ?, attempts = 0, next_attempt_at = ?, updated_at = ?"
                " WHERE id = ? AND status = ?",
                (QUEUED, now, now, email_id, FAILED),
            )
        return cur.rowcount == 1

    def recover_interrupted(self) -> int:
        """On startup, put back anything left 'sending' by a crash or restart."""
        now = to_db(utcnow())
        with self._lock:
            cur = self._conn.execute(
                "UPDATE emails SET status = ?, next_attempt_at = ?, updated_at = ? WHERE status = ?",
                (QUEUED, now, now, SENDING),
            )
        return cur.rowcount

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM emails GROUP BY status").fetchall()
        result = {s: 0 for s in STATUSES}
        result.update({r["status"]: r["n"] for r in rows})
        return result
