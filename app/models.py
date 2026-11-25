import base64
import binascii
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class Attachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content_type: str = "application/octet-stream"
    content_base64: str = Field(min_length=1)

    @field_validator("content_base64")
    @classmethod
    def must_be_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 is not valid base64") from exc
        return value

    @property
    def size_bytes(self) -> int:
        return len(base64.b64decode(self.content_base64))


class EmailRequest(BaseModel):
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = Field(default_factory=list)
    bcc: list[EmailStr] = Field(default_factory=list)
    reply_to: list[EmailStr] = Field(default_factory=list)
    subject: str = Field(min_length=1, max_length=998)
    body: str
    body_type: Literal["html", "text"] = "html"
    attachments: list[Attachment] = Field(default_factory=list)
    # When to send. Omit to send as soon as possible. Times without a zone are treated as UTC.
    send_at: datetime | None = None

    @field_validator("send_at")
    @classmethod
    def assume_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def limit_recipients(self):
        # Exchange Online caps a single message at 500 recipients.
        if len(self.to) + len(self.cc) + len(self.bcc) > 500:
            raise ValueError("a single email can have at most 500 recipients")
        return self

    @property
    def total_attachment_bytes(self) -> int:
        return sum(a.size_bytes for a in self.attachments)


class EmailQueued(BaseModel):
    id: str
    status: str
    send_at: datetime


class EmailStatus(BaseModel):
    id: str
    app: str
    status: Literal["queued", "sending", "sent", "failed"]
    subject: str
    recipients: list[str]
    attempts: int
    last_error: str | None
    send_at: datetime
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None

    @classmethod
    def from_record(cls, r) -> "EmailStatus":
        return cls(**{f: getattr(r, f) for f in cls.model_fields})
