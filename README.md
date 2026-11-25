# graphapiserver

A small central service that sends email through Microsoft Graph, so your apps call one HTTP API and don't each need their own Graph setup.

> Status: step 1 of 4 (core send API). Queueing, scheduling, the failure log and Docker packaging come next.

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

Fields: `to` (required), `cc`, `bcc`, `reply_to`, `subject`, `body`, `body_type` (`html` or `text`), `attachments`.
Attachments total up to 3 MB by default (`MAX_ATTACHMENT_BYTES`).

Responses: `202` sent, `401` bad API key, `413` attachments too large, `422` invalid request, `502` Graph rejected the send (the error is in `detail` and in the logs).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a fake Graph, so nothing is really sent.
