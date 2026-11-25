from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, read from environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "delegated": a person signs in once (python -m app.signin) and mail is sent as them.
    # "application": app-only client credentials, sending from SENDER_ADDRESS.
    auth_mode: Literal["delegated", "application"] = "delegated"

    tenant_id: str
    client_id: str
    client_secret: str | None = None  # application mode only
    sender_address: str | None = None  # application mode only; delegated sends as the signed-in account
    # Where the delegated sign-in (refresh token) is kept. Put it on the data volume.
    token_cache_path: str = "data/token_cache.json"

    # "app1:key1,app2:key2" -> {"key1": "app1", "key2": "app2"}
    api_keys: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    save_to_sent_items: bool = False
    # Graph's single-request sendMail limit is ~4 MB including base64 overhead,
    # so cap raw attachment bytes at 3 MB by default.
    max_attachment_bytes: int = 3 * 1024 * 1024

    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_timeout_seconds: float = 30.0

    # Queue / worker
    database_path: str = "data/emails.db"
    max_attempts: int = 5
    # Backoff after a retryable failure: base * 2^(attempt-1), capped. Graph's Retry-After wins if longer.
    retry_base_seconds: float = 30.0
    retry_max_seconds: float = 1800.0
    # Pause between sends so a burst of scheduled emails goes out at a steady pace.
    send_interval_seconds: float = 0.5
    worker_poll_seconds: float = 2.0
    run_worker: bool = True

    @model_validator(mode="after")
    def check_mode(self):
        if self.auth_mode == "application" and not (self.client_secret and self.sender_address):
            raise ValueError("AUTH_MODE=application needs CLIENT_SECRET and SENDER_ADDRESS")
        return self

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, value):
        if isinstance(value, dict):
            return value
        keys: dict[str, str] = {}
        for pair in (value or "").split(","):
            pair = pair.strip()
            if not pair:
                continue
            name, sep, key = pair.partition(":")
            if not sep or not name.strip() or not key.strip():
                raise ValueError(f"API_KEYS entry {pair!r} must look like name:key")
            keys[key.strip()] = name.strip()
        return keys


@lru_cache
def get_settings() -> Settings:
    return Settings()
