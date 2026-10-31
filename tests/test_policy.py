import asyncio
import subprocess
import sys

import httpx
import pytest

from async_instalib import (
    AccountPaused,
    AccountRestricted,
    ChallengeRequired,
    ClientOptions,
    Instagram,
    LoginFlowUnsupported,
    RequestBudgetExceeded,
    RequestPolicy,
    Session,
    SessionInUse,
)


def saved_session(path):
    Session(authorization="Bearer synthetic", user_id="42").save(path)


def account_response():
    return httpx.Response(200, json={"user": {"pk": 42, "username": "synthetic"}})


async def test_budget_is_saved_before_dispatch_and_survives_restart(tmp_path):
    path = tmp_path / "session.json"
    saved_session(path)
    options = ClientOptions(policy=RequestPolicy(min_interval=0, max_requests=1))
    calls = 0

    def server(request):
        nonlocal calls
        calls += 1
        assert len(Session.load(path).request_times) == 1
        return account_response()

    async with Instagram.from_session_file(path, options, httpx.MockTransport(server)) as client:
        await client.account.me()
    async with Instagram.from_session_file(path, options, httpx.MockTransport(server)) as client:
        with pytest.raises(RequestBudgetExceeded) as caught:
            await client.account.me()
        assert caught.value.budget == "request"
        assert caught.value.retry_after > 0
    assert calls == 1


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (
            {"message": "challenge_required", "challenge": {"url": "https://instagram.com/"}},
            ChallengeRequired,
        ),
        ({"message": "feedback_required", "status": "fail"}, AccountRestricted),
    ],
)
async def test_account_failure_pauses_queued_work_and_restart(tmp_path, payload, error):
    path = tmp_path / "session.json"
    saved_session(path)
    options = ClientOptions(policy=RequestPolicy(min_interval=0))
    calls = 0

    def server(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json=payload) if calls == 1 else account_response()

    async with Instagram.from_session_file(path, options, httpx.MockTransport(server)) as client:
        outcomes = await asyncio.gather(
            client.account.me(), client.account.me(), return_exceptions=True
        )
        assert isinstance(outcomes[0], error)
        assert isinstance(outcomes[1], AccountPaused)
        assert Session.load(path).paused_reason
    async with Instagram.from_session_file(path, options, httpx.MockTransport(server)) as client:
        with pytest.raises(AccountPaused):
            await client.account.me()
        assert calls == 1
        count = len(client.session.request_times)
        await client.resume_after_review()
        assert len(client.session.request_times) == count
        assert (await client.account.me()).id == "42"


async def test_one_owner_per_session_file_including_another_process(tmp_path):
    path = tmp_path / "session.json"
    async with Instagram.from_session_file(path):
        with pytest.raises(SessionInUse):
            Instagram.from_session_file(path)
        code = (
            "import sys; from pathlib import Path; "
            "from async_instalib.session.lease import SessionLease; "
            "SessionLease(Path(sys.argv[1]))"
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code, str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "SessionInUse" in result.stderr
    async with Instagram.from_session_file(path):
        pass


async def test_concurrent_admissions_obey_pacing():
    loop = asyncio.get_running_loop()
    times = []

    def server(request):
        times.append(loop.time())
        return account_response()

    options = ClientOptions(max_connections=3, policy=RequestPolicy(min_interval=0.03))
    async with Instagram(
        Session(authorization="Bearer synthetic", user_id="42"),
        options,
        httpx.MockTransport(server),
    ) as client:
        await asyncio.gather(*(client.account.me() for _ in range(3)))
    assert all(b - a >= 0.025 for a, b in zip(times, times[1:], strict=False))


async def test_login_budget_blocks_before_any_http(key_headers):
    calls = 0

    def server(request):
        nonlocal calls
        calls += 1
        if request.method == "GET":
            return httpx.Response(405, headers=key_headers)
        return httpx.Response(400, json={"status": "fail", "error_type": "needs_upgrade"})

    options = ClientOptions(policy=RequestPolicy(min_interval=0, max_login_attempts=1))
    async with Instagram(options=options, transport=httpx.MockTransport(server)) as client:
        with pytest.raises(LoginFlowUnsupported):
            await client.auth.login_legacy("synthetic", "synthetic-password")
        with pytest.raises(RequestBudgetExceeded) as caught:
            await client.auth.login_legacy("synthetic", "synthetic-password")
        assert caught.value.budget == "login"
    assert calls == 2
