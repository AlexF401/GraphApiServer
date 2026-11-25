import json
import os

import httpx
import pytest
import respx

from app.auth import DelegatedAuth, NotSignedIn
from app.config import Settings
from app.graph import GraphError, GraphMailer
from app.models import EmailRequest
from app.signin import main as signin_main
from app.worker import QueueWorker

ME_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
PAYLOAD = {"to": ["someone@example.com"], "subject": "Hi", "body": "x"}


class FakePublicApp:
    """Stands in for msal.PublicClientApplication; accounts live in the real token cache file."""

    def __init__(self, cache, silent_result=None):
        self.cache = cache
        self.silent_result = silent_result if silent_result is not None else {"access_token": "user-token"}
        self.silent_calls = 0

    def get_accounts(self):
        data = json.loads(self.cache.serialize() or "{}")
        return [{"username": a["username"], "home_account_id": k} for k, a in data.get("Account", {}).items()]

    def acquire_token_silent_with_error(self, scopes, account):
        self.silent_calls += 1
        if isinstance(self.silent_result, Exception):
            raise self.silent_result
        return self.silent_result

    def remove_account(self, account):
        data = json.loads(self.cache.serialize())
        data["Account"].pop(account["home_account_id"])
        self.cache.deserialize(json.dumps(data))
        self.cache.has_state_changed = True


def write_signed_in_cache(path, username="sender@example.com"):
    path.write_text(json.dumps({"Account": {"acc1": {"username": username}}}))


@pytest.fixture
def dsettings(tmp_path):
    return Settings(
        _env_file=None,
        tenant_id="t",
        client_id="c",
        token_cache_path=str(tmp_path / "token_cache.json"),
        database_path=":memory:",
        run_worker=False,
    )


def make_auth(dsettings, silent_result=None):
    auth = DelegatedAuth(dsettings)
    auth.msal_app = FakePublicApp(auth.cache, silent_result)
    return auth


def test_delegated_is_default_and_needs_no_secret(dsettings):
    assert dsettings.auth_mode == "delegated"
    assert dsettings.client_secret is None


def test_application_mode_requires_secret_and_sender():
    with pytest.raises(ValueError):
        Settings(_env_file=None, auth_mode="application", tenant_id="t", client_id="c")


def test_not_signed_in(dsettings):
    auth = make_auth(dsettings)
    assert auth.ready() is False
    assert auth.status() == {"mode": "delegated", "signed_in": False, "account": None}
    with pytest.raises(NotSignedIn):
        auth.get_token()


def test_picks_up_sign_in_written_by_another_process(dsettings, tmp_path):
    auth = make_auth(dsettings)
    assert not auth.ready()
    write_signed_in_cache(tmp_path / "token_cache.json")  # what `python -m app.signin` does
    assert auth.ready()
    assert auth.status()["account"] == "sender@example.com"
    assert auth.get_token() == "user-token"


def test_expired_sign_in_is_dropped(dsettings, tmp_path):
    write_signed_in_cache(tmp_path / "token_cache.json")
    auth = make_auth(dsettings, {"error": "invalid_grant", "error_description": "AADSTS700082: refresh token expired"})
    with pytest.raises(NotSignedIn) as exc:
        auth.get_token()
    assert "AADSTS700082" in str(exc.value)
    assert auth.ready() is False
    # Saved to disk, private to the service user.
    assert json.loads((tmp_path / "token_cache.json").read_text())["Account"] == {}
    assert oct(os.stat(tmp_path / "token_cache.json").st_mode & 0o777) == "0o600"


def test_network_error_getting_token_is_retryable(dsettings, tmp_path):
    write_signed_in_cache(tmp_path / "token_cache.json")
    auth = make_auth(dsettings, ConnectionError("no route"))
    mailer = GraphMailer(dsettings, http=httpx.Client(), auth=auth)
    with pytest.raises(GraphError) as exc:
        mailer.send(EmailRequest.model_validate(PAYLOAD))
    assert exc.value.retryable and not exc.value.not_signed_in
    assert auth.ready()  # sign-in kept


@respx.mock
def test_delegated_sends_via_me(dsettings, tmp_path):
    write_signed_in_cache(tmp_path / "token_cache.json")
    route = respx.post(ME_URL).mock(return_value=httpx.Response(202))
    GraphMailer(dsettings, http=httpx.Client(), auth=make_auth(dsettings)).send(EmailRequest.model_validate(PAYLOAD))
    assert route.calls.last.request.headers["Authorization"] == "Bearer user-token"


def test_worker_waits_while_signed_out_then_sends(store, mailer, dsettings):
    worker = QueueWorker(store, mailer, dsettings)
    record = store.enqueue("billing", PAYLOAD)
    mailer.signed_in = False
    assert worker.run_once() is False
    assert store.get(record.id).status == "queued"
    assert store.get(record.id).attempts == 0

    mailer.signed_in = True
    assert worker.run_once() is True
    assert store.get(record.id).status == "sent"


def test_sign_in_lost_mid_send_does_not_use_up_attempts(store, mailer, dsettings):
    worker = QueueWorker(store, mailer, dsettings)
    record = store.enqueue("billing", PAYLOAD)
    err = GraphError("sign-in expired", retryable=True)
    err.not_signed_in = True
    mailer.errors.append(err)
    worker.run_once()
    r = store.get(record.id)
    assert (r.status, r.attempts) == ("queued", 0)
    assert "sign-in expired" in r.last_error


def test_signin_status_and_sign_out_commands(dsettings, tmp_path, monkeypatch, capsys):
    import app.signin as signin

    monkeypatch.setattr(signin, "get_settings", lambda: dsettings)
    monkeypatch.setattr(signin, "DelegatedAuth", lambda s: make_auth(s))

    assert signin_main(["--status"]) == 1
    assert "NOT signed in" in capsys.readouterr().out

    write_signed_in_cache(tmp_path / "token_cache.json")
    assert signin_main(["--status"]) == 0
    assert "Signed in as sender@example.com" in capsys.readouterr().out

    assert signin_main(["--sign-out"]) == 0
    assert "Signed out sender@example.com" in capsys.readouterr().out
    assert signin_main(["--status"]) == 1


def test_health_and_auth_status_endpoints(client, mailer):
    assert client.get("/health").json()["signed_in"] is True
    mailer.signed_in = False
    assert client.get("/health").json()["signed_in"] is False
    assert client.get("/auth/status").status_code == 401
    body = client.get("/auth/status", headers={"X-API-Key": "key-billing"}).json()
    assert body == {"mode": "delegated", "signed_in": False, "account": None}
