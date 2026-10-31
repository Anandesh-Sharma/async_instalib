"""Local interactive entry point; passwords are read from the terminal without echo."""

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

from . import Instagram, InstagramError, TwoFactorMethod, TwoFactorRequired
from .triggers import Event, TriggerOptions, Triggers, Webhook


async def run(args: argparse.Namespace) -> int:
    async with Instagram.from_session_file(args.session) as client:
        session = client.session
        try:
            if args.command == "login" and not session.authenticated:
                if not sys.stdin.isatty():
                    raise ValueError("Login requires an interactive terminal for hidden input.")
                # This interactive CLI waits for its sole user; the library itself never prompts.
                if not session.pending_two_factor:
                    username = input("Instagram username, phone, or email: ").strip()  # noqa: ASYNC250
                    password = getpass.getpass("Instagram password (hidden): ")
                    try:
                        await client.auth.login(username, password)
                    except TwoFactorRequired:
                        pass
                    finally:
                        del password
                if session.pending_two_factor:
                    method = input("Code method [totp/sms, default totp]: ").strip().lower()  # noqa: ASYNC250
                    if method not in {"", "totp", "sms"}:
                        raise ValueError("Choose totp or sms.")
                    code = getpass.getpass("Six-digit verification code (hidden): ")
                    await client.auth.verify_two_factor(
                        code, TwoFactorMethod.SMS if method == "sms" else TwoFactorMethod.TOTP
                    )
            user = await client.account.me()
            print(json.dumps({"ok": True, "account_identity_verified": user.id == session.user_id}))
            return 0
        except InstagramError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": type(error).__name__,
                        "message": str(error),
                        "status_code": error.info.status_code,
                        "error_type": error.info.error_type,
                    }
                )
            )
            return 1


async def watch(args: argparse.Namespace) -> int:
    """Print new account events and post them to the given webhooks until interrupted."""
    secret = os.environ.get(args.secret_env) if args.secret_env else None
    if args.secret_env and not secret:
        raise ValueError(f"Environment variable {args.secret_env} is empty or unset.")
    events = frozenset(filter(None, (args.events or "").split(",")))
    state = args.state or args.session.with_name(args.session.name + ".triggers.json")
    options = TriggerOptions(
        interval=args.interval, state_file=state, deliver_existing=args.deliver_existing
    )
    async with Instagram.from_session_file(args.session) as client:
        async with Triggers(client, options) as triggers:
            for url in args.webhook or []:
                triggers.add_webhook(Webhook(url, secret, events))

            @triggers.on(*events)
            async def show(event: Event) -> None:
                actor = event.actor.username if event.actor else "-"
                line = {"type": event.type, "actor": actor, "text": event.text}
                print(json.dumps(line), flush=True)

            try:
                await triggers.run()
            except InstagramError as error:
                print(json.dumps({"ok": False, "error": type(error).__name__}))
                return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("login", "me", "watch"))
    parser.add_argument("--session", type=Path, required=True, help="Private session file path")
    watching = parser.add_argument_group("watch")
    watching.add_argument("--webhook", action="append", help="URL to POST each event to")
    watching.add_argument("--secret-env", help="Environment variable holding the signing secret")
    watching.add_argument("--events", help="Comma-separated event types; default all")
    watching.add_argument("--interval", type=float, default=60.0, help="Seconds between polls")
    watching.add_argument(
        "--state", type=Path, help="Delivered-event file (default: beside session)"
    )
    watching.add_argument(
        "--deliver-existing", action="store_true", help="Also deliver events present at start"
    )
    args = parser.parse_args()
    args.session = args.session.expanduser()
    try:
        return asyncio.run(watch(args) if args.command == "watch" else run(args))
    except (ValueError, OSError) as error:
        print(f"Unable to continue: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
