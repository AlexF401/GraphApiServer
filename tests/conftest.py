import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.graph import GraphError
from app.main import app
from app.store import EmailStore
from app.worker import QueueWorker


class FakeMsal:
    def __init__(self, result=None):
        self.result = result or {"access_token": "fake-token"}

    def acquire_token_for_client(self, scopes):
        return self.result


class FakeMailer:
    """Records sends; raise errors by appending to .errors."""

    def __init__(self):
        self.sent = []
        self.errors: list[GraphError] = []
        self.signed_in = True
        self.auth = self

    mode = "delegated"

    def ready(self):
        return self.signed_in

    def status(self):
        return {"mode": "delegated", "signed_in": self.signed_in, "account": "sender@example.com" if self.signed_in else None}

    def send(self, email):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(email)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        auth_mode="application",
        tenant_id="t",
        client_id="c",
        client_secret="s",
        sender_address="noreply@example.com",
        api_keys="billing:key-billing,crm:key-crm",
        max_attachment_bytes=1000,
        database_path=":memory:",
        run_worker=False,
        max_attempts=3,
        retry_base_seconds=10,
        retry_max_seconds=100,
    )


@pytest.fixture
def store():
    s = EmailStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def worker(store, mailer, settings):
    return QueueWorker(store, mailer, settings)


@pytest.fixture
def client(settings, store, mailer):
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.store = store
    app.state.mailer = mailer
    # lifespan calls get_settings() directly, so patch the cached function too
    get_settings.cache_clear()
    import app.main as main_module

    original = main_module.get_settings
    main_module.get_settings = lambda: settings
    try:
        with TestClient(app) as c:
            yield c
    finally:
        main_module.get_settings = original
        app.dependency_overrides.clear()
        app.state.store = None
        app.state.mailer = None
