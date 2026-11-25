"""Getting Graph access tokens.

Two modes:

* delegated (default): a person signs in once with a device code
  (``python -m app.signin``) and mail is sent as that account. MSAL keeps a
  refresh token in a cache file on the data volume, so the service stays
  signed in across restarts and refreshes access tokens by itself.
* application: app-only client credentials (client secret), sending from
  SENDER_ADDRESS.
"""

import json
import logging
import os
import threading
from pathlib import Path

import msal

from .config import Settings

log = logging.getLogger("graphapiserver.auth")

DELEGATED_SCOPES = ["https://graph.microsoft.com/Mail.Send", "https://graph.microsoft.com/User.Read"]
APP_SCOPES = ["https://graph.microsoft.com/.default"]

# MSAL errors meaning the saved sign-in is no longer usable and a person must sign in again.
REAUTH_ERRORS = {"invalid_grant", "interaction_required", "consent_required", "login_required"}


class AuthError(Exception):
    """Could not get a token. ``retryable`` is True for transient problems."""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class NotSignedIn(AuthError):
    """Delegated mode has no usable sign-in. Emails wait in the queue until someone signs in."""

    def __init__(self, message: str = "not signed in: run the sign-in command (see docs/SETUP.md)"):
        super().__init__(message, retryable=True)


def _authority(settings: Settings) -> str:
    return f"https://login.microsoftonline.com/{settings.tenant_id}"


class AppOnlyAuth:
    mode = "application"

    def __init__(self, settings: Settings, msal_app=None):
        self.settings = settings
        # Built lazily: MSAL contacts Entra on construction, and the service should
        # still start (and queue mail) if Entra is briefly unreachable.
        self.msal_app = msal_app

    def _msal(self):
        if self.msal_app is None:
            self.msal_app = msal.ConfidentialClientApplication(
                client_id=self.settings.client_id,
                client_credential=self.settings.client_secret,
                authority=_authority(self.settings),
            )
        return self.msal_app

    def ready(self) -> bool:
        return True

    def status(self) -> dict:
        return {"mode": self.mode, "signed_in": True, "account": self.settings.sender_address}

    def get_token(self) -> str:
        try:
            result = self._msal().acquire_token_for_client(scopes=APP_SCOPES)
        except Exception as exc:  # MSAL raises if Entra is unreachable or the tenant is wrong
            raise AuthError(f"could not get Graph token: {exc}", retryable=True) from exc
        if "access_token" not in result:
            detail = result.get("error_description") or result.get("error") or "unknown error"
            # Bad credentials won't fix themselves, but a network blip reaching Entra will.
            retryable = result.get("error") not in ("invalid_client", "unauthorized_client", "invalid_request")
            raise AuthError(f"could not get Graph token: {detail}", retryable=retryable)
        return result["access_token"]


class DelegatedAuth:
    mode = "delegated"

    def __init__(self, settings: Settings, msal_app=None):
        self.settings = settings
        self.cache_path = Path(settings.token_cache_path)
        self.cache = msal.SerializableTokenCache()
        self._cache_mtime: float | None = None
        self._lock = threading.RLock()
        self.msal_app = msal_app
        self._load_cache()

    # --- token cache file -------------------------------------------------
    # The sign-in command runs as a separate process, so re-read the file
    # whenever it changes and write it back whenever MSAL updates it.

    def _load_cache(self) -> None:
        try:
            mtime = self.cache_path.stat().st_mtime
        except FileNotFoundError:
            if self._cache_mtime is not None:  # file was deleted: forget the sign-in
                self.cache = msal.SerializableTokenCache()
                self.msal_app = None
                self._cache_mtime = None
            return
        if mtime != self._cache_mtime:
            self.cache.deserialize(self.cache_path.read_text())
            self._cache_mtime = mtime

    def _save_cache(self) -> None:
        if not self.cache.has_state_changed:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        # The cache holds a refresh token: keep it readable by this user only.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(self.cache.serialize())
        os.replace(tmp, self.cache_path)
        self._cache_mtime = self.cache_path.stat().st_mtime
        self.cache.has_state_changed = False

    def _msal(self):
        if self.msal_app is None:
            self.msal_app = msal.PublicClientApplication(
                client_id=self.settings.client_id,
                authority=_authority(self.settings),
                token_cache=self.cache,
            )
        return self.msal_app

    def _account(self):
        """The signed-in account, read from the cache only (no network)."""
        accounts = self._msal().get_accounts()
        return accounts[0] if accounts else None

    # --- used by the service ----------------------------------------------

    def _cached_username(self) -> str | None:
        # Read straight from the cache so status checks never need the network.
        accounts = list(self.cache.search(msal.TokenCache.CredentialType.ACCOUNT))
        return accounts[0].get("username") if accounts else None

    def ready(self) -> bool:
        with self._lock:
            self._load_cache()
            return self._cached_username() is not None

    def status(self) -> dict:
        with self._lock:
            self._load_cache()
            username = self._cached_username()
            return {"mode": self.mode, "signed_in": username is not None, "account": username}

    def get_token(self) -> str:
        with self._lock:
            self._load_cache()
            try:
                account = self._account()
                if account is None:
                    raise NotSignedIn()
                result = self._msal().acquire_token_silent_with_error(DELEGATED_SCOPES, account=account)
            except AuthError:
                raise
            except Exception as exc:  # network trouble reaching Entra
                raise AuthError(f"could not get Graph token: {exc}", retryable=True) from exc
            finally:
                self._save_cache()

            if result and "access_token" in result:
                return result["access_token"]

            detail = (result or {}).get("error_description") or (result or {}).get("error") or "no usable sign-in"
            if result is None or result.get("error") in REAUTH_ERRORS:
                # The refresh token expired or was revoked: drop it so the worker pauses
                # (instead of failing emails) until someone signs in again.
                log.error("sign-in for %s is no longer valid, sign in again: %s", account["username"], detail)
                self._msal().remove_account(account)
                self._save_cache()
                raise NotSignedIn(f"sign-in expired or was revoked, sign in again: {detail}")
            raise AuthError(f"could not get Graph token: {detail}", retryable=True)

    # --- used by the sign-in command ----------------------------------------

    def start_device_flow(self) -> dict:
        try:
            with self._lock:
                flow = self._msal().initiate_device_flow(scopes=DELEGATED_SCOPES)
        except Exception as exc:  # can't reach Entra, or TENANT_ID is wrong
            raise AuthError(f"could not reach Microsoft sign-in: {exc}", retryable=True) from exc
        if "user_code" not in flow:
            raise AuthError(
                f"could not start sign-in: {flow.get('error_description') or json.dumps(flow)}", retryable=False
            )
        return flow

    def finish_device_flow(self, flow: dict) -> str:
        """Blocks until the person finishes signing in (or the code expires). Returns the username."""
        try:
            result = self._msal().acquire_token_by_device_flow(flow)
        except Exception as exc:
            raise AuthError(f"lost contact with Microsoft sign-in: {exc}", retryable=True) from exc
        with self._lock:
            if "access_token" not in result:
                raise AuthError(
                    f"sign-in failed: {result.get('error_description') or result.get('error')}", retryable=False
                )
            # Keep exactly one account: signing in as someone new replaces the old sign-in.
            username = result.get("id_token_claims", {}).get("preferred_username")
            for acc in self._msal().get_accounts():
                if acc["username"] != username:
                    self._msal().remove_account(acc)
            self._save_cache()
            return username or self._account()["username"]

    def sign_out(self) -> list[str]:
        with self._lock:
            self._load_cache()
            removed = []
            for acc in self._msal().get_accounts():
                self._msal().remove_account(acc)
                removed.append(acc["username"])
            self._save_cache()
            return removed


def build_auth(settings: Settings):
    return DelegatedAuth(settings) if settings.auth_mode == "delegated" else AppOnlyAuth(settings)
