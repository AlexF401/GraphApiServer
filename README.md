# graphapiserver

A small central service that sends email through Microsoft Graph, so your apps call one HTTP API and don't each need their own Graph setup.

> Status: step 2 of 4 (send API plus queue, scheduling, retries and failure log). Docker packaging comes next.

## Setup

1. In Entra ID, the app registration needs the **application** permission `Mail.Send`, with admin consent.
2. Copy `.env.example` to `.env` and fill in the tenant ID, client ID, secret, sender address and one API key per calling app.
3. Install and run:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Sending an email

```bash
curl -X POST http://localhost:8000/emails \
  -H "X-API-Key: replace-with-long-random-key" \
  -H "Content-Type: application/json" \
  -d '{
        "to": ["someone@example.com"],
        "subject": "Monthly report",
        "body": "<p>Report attached.</p>",
        "attachments": [
          {"name": "report.txt", "content_type": "text/plain", "content_base64": "aGVsbG8="}
        ]
      }'
```

Fields: `to` (required), `cc`, `bcc`, `reply_to`, `subject`, `body`, `body_type` (`html` or `text`), `attachments`, `send_at`.
Attachments total up to 3 MB by default (`MAX_ATTACHMENT_BYTES`).

The call returns `202` straight away with an `id`; a background worker does the actual send:

```json
{"id": "3f2c...", "status": "queued", "send_at": "2026-11-25T17:25:57Z"}
```

Other responses: `401` bad API key, `413` attachments too large, `422` invalid request.

### Scheduling

Pass `send_at` (ISO 8601, e.g. `"2026-12-01T08:00:00-05:00"`; no zone means UTC) to send later. If many emails are due at once, the worker sends them in order with a short pause between each (`SEND_INTERVAL_SECONDS`).

### Retries and failures

If Graph throttles (429), times out, has a 5xx or can't be reached, the email is retried with exponential backoff (30s, 60s, 120s... capped at 30 min, or longer if Graph sends `Retry-After`), up to `MAX_ATTEMPTS` (5). Errors that won't fix themselves (bad recipient, access denied, bad credentials) fail straight away.

Every failure is logged to stdout with the email id, app, recipients and error, and kept in the database.

| Endpoint | What it does |
| --- | --- |
| `GET /emails/{id}` | Status of one email: `queued`, `sending`, `sent` or `failed`, with `attempts` and `last_error` |
| `GET /emails?status=failed` | Failure log for your app (newest first; `limit`, `offset`) |
| `POST /emails/{id}/retry` | Put a failed email back in the queue |
| `GET /health` | Liveness plus queue counts |

Each app only sees its own emails.

The queue lives in a SQLite file (`DATABASE_PATH`), so it survives restarts; anything mid-send during a crash is retried on startup. Each server keeps its own queue.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a fake Graph, so nothing is really sent.
