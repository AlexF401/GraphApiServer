from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, read from environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tenant_id: str
    client_id: str
    client_secret: str
    sender_address: str

    # "app1:key1,app2:key2" -> {"key1": "app1", "key2": "app2"}
    api_keys: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    save_to_sent_items: bool = False
    # Graph's single-request sendMail limit is ~4 MB including base64 overhead,
    # so cap raw attachment bytes at 3 MB by default.
    max_attachment_bytes: int = 3 * 1024 * 1024

    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_timeout_seconds: float = 30.0

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
