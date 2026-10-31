import asyncio
import json

import httpx
import pytest

from async_instalib import (
    ChallengeRequired,
    ClientOptions,
    Instagram,
    LoginRequired,
    NetworkError,
    ProtocolError,
    RequestPolicy,
    Session,
)
from async_instalib.transport import Transport


def authenticated():
    return Session(authorization="Bearer synthetic-token", user_id="42")


async def test_rejected_session_clears_credentials():
    def server(request):
        return httpx.Response(401, json={"message": "login_required", "status": "fail"})

    async with Instagram(authenticated(), transport=httpx.MockTransport(server)) as client:
        with pytest.raises(LoginRequired):
            await client.account.me()
        assert not client.session.authenticated
        assert client.session.authorization is None
        with pytest.raises(LoginRequired):
            await client.account.me()


async def test_identity_mismatch_is_rejected():
    def server(request):
        return httpx.Response(200, json={"user": {"pk": 999, "username": "someone_else"}})

    async with Instagram(authenticated(), transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ProtocolError, match="identity"):
            await client.account.me()


async def test_response_state_rotates_and_telemetry_is_not_replayed():
    observed = []
    session = Session.from_headers(
        {
            "authorization": "Bearer first",
            "ig-u-ds-user-id": "42",
            "x-fb-conn-uuid": "captured-connection-only",
            "x-pigeon-session-id": "captured-old-session",
        }
    )

    def server(request):
        observed.append(request.headers)
        return httpx.Response(
            200,
            headers={
                "ig-set-authorization": "Bearer second",
                "ig-set-ig-u-rur": "synthetic-new-route",
                "x-ig-set-www-claim": "synthetic-new-claim",
            },
            json={"user": {"pk": 42, "username": "synthetic_user"}},
        )

    async with Instagram(session, transport=httpx.MockTransport(server)) as client:
        await client.account.me()
        await client.account.me()
    assert observed[0]["authorization"] == "Bearer first"
    assert observed[1]["authorization"] == "Bearer second"
    assert observed[1]["ig-u-rur"] == "synthetic-new-route"
    assert observed[1]["x-ig-www-claim"] == "synthetic-new-claim"
    assert "x-fb-conn-uuid" not in observed[0]
    assert observed[0]["x-pigeon-session-id"] != "captured-old-session"


async def test_redirect_never_forwards_bearer():
    calls = []

    def server(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://other.invalid/"})

    async with Instagram(authenticated(), transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ProtocolError, match="redirect"):
            await client.account.me()
    assert calls == ["i.instagram.com"]


async def test_challenge_on_http_200_is_not_success():
    def server(request):
        return httpx.Response(
            200,
            json={"challenge": {"url": "https://www.instagram.com/challenge/synthetic/"}},
        )

    async with Instagram(authenticated(), transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ChallengeRequired) as caught:
            await client.account.me()
        assert caught.value.challenge_url.startswith("https://www.instagram.com/")


async def test_network_errors_are_redacted_without_retry():
    calls = 0

    def server(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("proxy password=synthetic-secret", request=request)

    async with Instagram(authenticated(), transport=httpx.MockTransport(server)) as client:
        with pytest.raises(NetworkError) as caught:
            await client.account.me()
        assert "synthetic-secret" not in str(caught.value)
        assert caught.value.__suppress_context__
    assert calls == 1


@pytest.mark.parametrize("body", [b"<html>not JSON</html>", b"[]", b'{"user":null}'])
async def test_malformed_account_responses(body):
    async with Instagram(
        authenticated(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(ProtocolError):
            await client.account.me()


@pytest.mark.parametrize(
    "endpoint",
    ["https://other.invalid", "//other.invalid", "../login", "%2e%2e/login", "/login", "a\\b"],
)
async def test_endpoint_cannot_escape_api_origin(endpoint):
    transport = Transport(Session(), ClientOptions())
    try:
        with pytest.raises(ValueError):
            await transport.request("GET", endpoint)
    finally:
        await transport.aclose()


async def test_concurrency_limit_and_closed_lifecycle():
    active, peak = 0, 0

    async def server(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return httpx.Response(200, json={"user": {"pk": 42, "username": "synthetic_user"}})

    async with Instagram(
        authenticated(),
        ClientOptions(max_connections=2, policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    ) as client:
        results = await asyncio.gather(*(client.account.me() for _ in range(8)))
        assert len(results) == 8
    assert peak == 2
    with pytest.raises(RuntimeError, match="closed"):
        await client.account.me()


async def test_real_loopback_http_exchange_and_cookie_persistence(tmp_path):
    received = []

    async def handler(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            received.append(request.decode())
            body = json.dumps({"user": {"pk": 42, "username": "synthetic_user"}}).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Set-Cookie: loopback=synthetic; Path=/api/v1/; HttpOnly\r\n"
                b"Connection: close\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    options = ClientOptions(base_url=f"http://127.0.0.1:{port}/api/v1/")
    try:
        async with Instagram(authenticated(), options) as client:
            await client.account.me()
            client.session.save(tmp_path / "session.json")
        async with Instagram(Session.load(tmp_path / "session.json"), options) as client:
            await client.account.me()
    finally:
        server.close()
        await server.wait_closed()
    assert received[0].startswith("GET /api/v1/accounts/current_user/?edit=true HTTP/1.1")
    assert "Cookie: loopback=synthetic" in received[1]
