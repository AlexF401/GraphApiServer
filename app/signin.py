"""Sign the email service in to Microsoft (delegated mode).

Run inside the container:

    docker compose exec graphapiserver python -m app.signin            # sign in
    docker compose exec graphapiserver python -m app.signin --status   # who is signed in?
    docker compose exec graphapiserver python -m app.signin --sign-out # forget the sign-in
"""

import argparse
import sys

from .auth import AuthError, DelegatedAuth
from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.signin", description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="show who the service is signed in as")
    group.add_argument("--sign-out", action="store_true", help="remove the saved sign-in")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.auth_mode != "delegated":
        print(f"AUTH_MODE is '{settings.auth_mode}', so no sign-in is needed: the service uses its client secret.")
        return 0

    auth = DelegatedAuth(settings)

    if args.status:
        status = auth.status()
        if status["signed_in"]:
            print(f"Signed in as {status['account']}. Emails are sent from this account.")
            return 0
        print("NOT signed in. Emails will wait in the queue until you sign in:")
        print("  docker compose exec graphapiserver python -m app.signin")
        return 1

    if args.sign_out:
        removed = auth.sign_out()
        print(f"Signed out {', '.join(removed)}." if removed else "Nobody was signed in.")
        print("Emails will wait in the queue until someone signs in again.")
        return 0

    current = auth.status()
    if current["signed_in"]:
        print(f"Currently signed in as {current['account']}. Signing in again will replace it.\n")

    try:
        flow = auth.start_device_flow()
    except AuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Check TENANT_ID and CLIENT_ID, and that 'Allow public client flows' is turned on "
              "for the app registration (see docs/SETUP.md).", file=sys.stderr)
        return 2

    print("=" * 70)
    print(f"  1. On any computer or phone, open:  {flow['verification_uri']}")
    print(f"  2. Enter this code:                 {flow['user_code']}")
    print("  3. Sign in with the mailbox that should SEND the emails.")
    print("=" * 70)
    print(f"Waiting for you to finish (the code expires in about {int(flow.get('expires_in', 900)) // 60} minutes)...",
          flush=True)

    try:
        username = auth.finish_device_flow(flow)
    except AuthError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    print(f"\nSigned in as {username}. Queued emails will start sending within a few seconds.")
    print("You won't need to sign in again after restarts. Check any time with --status.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
