import argparse
from dataclasses import replace

import httpx
import pytest

from async_instalib import ClientOptions, ClientProfile, Instagram, RateLimited, Session
from async_instalib.cli import run


async def test_session_file_restores_identity_and_autosaves_on_exception(tmp_path):
    path = tmp_path / "private" / "account.session.json"
    async with Instagram.from_session_file(path) as client:
        original_device = client.session.device
        assert path.exists()
        client.session.authorization = "Bearer synthetic-secret"
        client.session.user_id = "42"

    def server(request):
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        assert request.headers["x-ig-device-id"] == original_device.id
        return httpx.Response(
            200,
            headers={"ig-set-authorization": "Bearer rotated"},
            json={"user": {"pk": 42, "username": "synthetic"}},
        )

    with pytest.raises(RuntimeError, match="caller error"):
        async with Instagram.from_session_file(
            path, transport=httpx.MockTransport(server)
        ) as client:
            assert (await client.account.me()).id == "42"
            raise RuntimeError("caller error")
    assert Session.load(path).authorization == "Bearer rotated"
    assert Session.load(path).device == original_device
    assert path.stat().st_mode & 0o777 == 0o600


def test_corrupt_file_is_not_silently_replaced(tmp_path):
    path = tmp_path / "account.session.json"
    path.write_text("invalid")
    with pytest.raises(ValueError):
        Instagram.from_session_file(path)
    assert path.read_text() == "invalid"


async def test_custom_profile_headers_are_consistent_and_persisted(tmp_path):
    path = tmp_path / "account.session.json"
    profile = ClientProfile().with_locale("en_IN", 19800)
    profile = replace(profile, country_code=91)
    session = Session(profile=profile, authorization="Bearer synthetic", user_id="42")
    session.save(path)
    supplied = {"X-IG-Connection-Type": "WIFI"}
    options = ClientOptions(extra_headers=supplied)
    supplied["X-IG-Connection-Type"] = "MUTATED"

    def server(request):
        assert "; en_IN;" in request.headers["user-agent"]
        assert request.headers["x-ig-app-locale"] == "en_IN"
        assert request.headers["accept-language"] == "en-IN"
        assert request.headers["x-ig-timezone-offset"] == "19800"
        assert request.headers["x-ig-connection-type"] == "WIFI"
        return httpx.Response(200, json={"user": {"pk": 42, "username": "synthetic"}})

    async with Instagram.from_session_file(path, options, httpx.MockTransport(server)) as client:
        await client.account.me()
    assert Session.load(path).profile == profile


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer x"},
        {"COOKIE": "sessionid=x"},
        {"Host": "other.invalid"},
        {"IG-U-RUR": "forged"},
        {"X-IG-Device-ID": "rotating"},
        {"User-Agent": "override"},
        {"X-Test": "line\r\ninjection"},
        {"Invalid Name": "value"},
        {"X-Test": "one", "x-test": "two"},
    ],
)
def test_extra_headers_reject_injection_and_owned_state(headers):
    with pytest.raises(ValueError):
        ClientOptions(extra_headers=headers)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeouts_fail_before_networking(timeout):
    with pytest.raises(ValueError):
        ClientOptions(timeout=timeout)


async def test_rate_limit_cooldown_survives_restart_and_blocks_early_requests(
    tmp_path, monkeypatch
):
    path = tmp_path / "account.session.json"
    Session(authorization="Bearer synthetic", user_id="42").save(path)
    now = [1000.0]
    monkeypatch.setattr("async_instalib.transport.http.time.time", lambda: now[0])
    calls = 0

    def server(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "120"}, content=b"not JSON")
        return httpx.Response(200, json={"user": {"pk": 42, "username": "synthetic"}})

    async with Instagram.from_session_file(path, transport=httpx.MockTransport(server)) as client:
        with pytest.raises(RateLimited) as original:
            await client.account.me()
        assert original.value.retry_after == 120
    now[0] += 30
    async with Instagram.from_session_file(path, transport=httpx.MockTransport(server)) as client:
        with pytest.raises(RateLimited) as blocked:
            await client.account.me()
        assert blocked.value.info.error_type == "local_cooldown"
        assert blocked.value.retry_after == 90
        assert calls == 1
        now[0] += 91
        assert (await client.account.me()).id == "42"
    assert calls == 2


async def test_cli_reuses_valid_session_without_password_prompt(tmp_path, monkeypatch, capsys):
    path = tmp_path / "account.session.json"
    Session(authorization="Bearer synthetic", user_id="42").save(path)

    def server(request):
        assert request.url.path.endswith("accounts/current_user/")
        return httpx.Response(200, json={"user": {"pk": 42, "username": "synthetic"}})

    factory = Instagram.from_session_file
    monkeypatch.setattr(
        Instagram,
        "from_session_file",
        lambda path: factory(path, transport=httpx.MockTransport(server)),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert await run(argparse.Namespace(command="login", session=path)) == 0
    assert '"ok": true' in capsys.readouterr().out


async def test_repeated_close_does_not_overwrite_a_newer_session(tmp_path):
    path = tmp_path / "account.session.json"
    client = Instagram.from_session_file(path)
    await client.aclose()
    newer = Session.load(path)
    newer.authorization = "Bearer newer"
    newer.user_id = "42"
    newer.save(path)
    await client.aclose()
    assert Session.load(path).authorization == "Bearer newer"


@pytest.mark.parametrize("hint", [None, "NaN", "Infinity", "not a delay"])
async def test_missing_or_invalid_wait_hint_has_bounded_fallback(hint):
    headers = {"retry-after": hint} if hint is not None else {}
    async with Instagram(
        Session(authorization="Bearer synthetic", user_id="42"),
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers=headers)),
    ) as client:
        with pytest.raises(RateLimited) as error:
            await client.account.me()
        assert error.value.retry_after == 60
