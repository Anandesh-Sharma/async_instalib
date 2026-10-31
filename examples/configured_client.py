"""Read your account with explicit settings; requires an existing private session.

Run: uv run python examples/configured_client.py --session /private/account.session.json
"""

import argparse
import asyncio
from pathlib import Path

from async_instalib import (
    AccountPaused,
    AuthOptions,
    ClientOptions,
    Instagram,
    InstagramError,
    RateLimited,
    RequestBudgetExceeded,
    RequestPolicy,
)


def client_options() -> ClientOptions:
    """Edit these application settings; the values are local budgets, not guarantees."""
    return ClientOptions(
        timeout=20,
        connect_timeout=5,
        read_timeout=20,
        write_timeout=20,
        pool_timeout=10,
        max_connections=1,
        max_keepalive_connections=1,
        keepalive_expiry=5,
        http2=True,
        max_response_bytes=16 * 1024 * 1024,
        proxy=None,
        ca_bundle=None,
        extra_headers={},
        policy=RequestPolicy(
            min_interval=2,
            max_requests=20,
            window_seconds=60,
            max_login_attempts=2,
            login_window_seconds=900,
            fallback_cooldown=120,
            pause_on_challenge=True,
            pause_on_restriction=True,
            pause_on_unsupported_login=True,
        ),
        auth=AuthOptions(
            trust_this_device=False,
            prepare_parameters={},
            client_parameters={},
            server_parameters={},
        ),
    )


async def main(session_path: Path) -> int:
    try:
        async with Instagram.from_session_file(session_path, client_options()) as instagram:
            await instagram.account.me(edit=False)
            print("Account verified; updated session saved automatically.")
        return 0
    except AccountPaused:
        print("Account paused. Resolve verification in Instagram before explicitly resuming.")
    except (RateLimited, RequestBudgetExceeded) as error:
        print("No automatic retry. Wait hint in seconds:", error.retry_after)
    except InstagramError as error:
        print(type(error).__name__, str(error))
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args().session)))
