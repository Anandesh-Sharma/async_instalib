import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from async_instalib import (
    BadCredentials,
    ChallengeRequired,
    Instagram,
    LoginFlowUnsupported,
    ProtocolError,
    RateLimited,
    Session,
    TwoFactorMethod,
    TwoFactorRequired,
)
from async_instalib.auth.password import PasswordKey

USER = {"pk": 42, "username": "synthetic_user"}
TOKEN = "Bearer IGT:synthetic-test-token"


def decrypt(encrypted, private_key):
    marker, version, timestamp, encoded = encrypted.split(":", 3)
    assert (marker, version) == ("#PWD_INSTAGRAM", "4")
    data = base64.b64decode(encoded)
    assert data[:2] == bytes([1, 9])
    nonce = data[2:14]
    length = int.from_bytes(data[14:16], "little")
    key = private_key.decrypt(data[16 : 16 + length], padding.PKCS1v15())
    tag = data[16 + length : 32 + length]
    return AESGCM(key).decrypt(nonce, data[32 + length :] + tag, timestamp.encode()).decode()


def test_password_envelope_is_decryptable_and_randomized(private_key, key_headers):
    key = PasswordKey(9, key_headers["ig-set-password-encryption-pub-key"])
    first, second = key.encrypt("synthetic-密碼&+"), key.encrypt("synthetic-密碼&+")
    assert decrypt(first, private_key) == "synthetic-密碼&+"
    assert decrypt(second, private_key) == "synthetic-密碼&+"
    assert first != second


@pytest.mark.parametrize("key", [PasswordKey(256, "x"), PasswordKey(1, "not a public key")])
def test_invalid_encryption_material_is_rejected(key):
    with pytest.raises(ProtocolError):
        key.encrypt("synthetic-password")


async def test_login_bootstrap_cookie_encryption_and_saved_session(
    key_headers, private_key, tmp_path
):
    calls = []

    def server(request):
        calls.append(request.url.path)
        if request.url.path.endswith("qe/sync/"):
            return httpx.Response(
                405, headers={**key_headers, "set-cookie": "csrftoken=test-csrf; Path=/; Secure"}
            )
        if request.url.path.endswith("accounts/login/"):
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert "csrftoken=test-csrf" in request.headers["cookie"]
            signed = parse_qs(request.content.decode())["signed_body"][0]
            body = json.loads(signed.removeprefix("SIGNATURE."))
            assert body["username"] == "test+user@example.invalid"
            assert decrypt(body["enc_password"], private_key) == "synthetic-密碼&+"
            assert body["_csrftoken"] == "test-csrf"
            assert "password" not in body
            return httpx.Response(
                200,
                headers={"ig-set-authorization": TOKEN, "ig-set-ig-u-rur": "synthetic-route"},
                json={"status": "ok", "logged_in_user": USER},
            )
        assert request.headers["authorization"] == TOKEN
        assert request.headers["ig-u-rur"] == "synthetic-route"
        return httpx.Response(200, json={"status": "ok", "user": USER})

    async with Instagram(transport=httpx.MockTransport(server)) as client:
        user = await client.auth.login_legacy(" test+user@example.invalid ", "synthetic-密碼&+")
        assert user.id == "42"
        assert client.session.authenticated
        path = tmp_path / "session.json"
        client.session.save(path)
    async with Instagram(Session.load(path), transport=httpx.MockTransport(server)) as restored:
        assert (await restored.account.me()).id == "42"
    assert calls.count("/api/v1/accounts/login/") == 1
    assert "synthetic-密碼" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


async def test_two_factor_survives_restart_and_invalid_code(key_headers, tmp_path):
    attempts = 0

    def server(request):
        nonlocal attempts
        if request.url.path.endswith("qe/sync/"):
            return httpx.Response(405, headers=key_headers)
        if request.url.path.endswith("accounts/login/"):
            return httpx.Response(
                400,
                json={
                    "status": "fail",
                    "two_factor_required": True,
                    "two_factor_info": {"two_factor_identifier": "synthetic-continuation"},
                },
            )
        body = json.loads(parse_qs(request.content.decode())["signed_body"][0].split(".", 1)[1])
        assert body["two_factor_identifier"] == "synthetic-continuation"
        assert body["verification_method"] == "3"
        attempts += 1
        if attempts == 1:
            return httpx.Response(400, json={"status": "fail", "error_type": "invalid_code"})
        return httpx.Response(
            200, headers={"ig-set-authorization": TOKEN}, json={"logged_in_user": USER}
        )

    async with Instagram(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(TwoFactorRequired):
            await client.auth.login_legacy("synthetic_user", "synthetic-password")
        assert not client.session.authenticated
        client.session.save(tmp_path / "session.json")
    async with Instagram(
        Session.load(tmp_path / "session.json"), transport=httpx.MockTransport(server)
    ) as client:
        from async_instalib import APIError

        with pytest.raises(APIError):
            await client.auth.verify_two_factor("000000")
        assert client.session.pending_two_factor is not None
        assert (await client.auth.verify_two_factor("123456", TwoFactorMethod.TOTP)).id == "42"
        assert client.session.pending_two_factor is None
        assert client.session.authenticated


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"error_type": "bad_password"}, BadCredentials),
        ({"error_type": "needs_upgrade"}, LoginFlowUnsupported),
        (
            {"message": "challenge_required", "challenge": {"url": "https://evil.invalid"}},
            ChallengeRequired,
        ),
    ],
)
async def test_login_failure_is_safe_and_never_retried(key_headers, payload, error):
    posts = 0

    def server(request):
        nonlocal posts
        if request.method == "GET":
            return httpx.Response(405, headers=key_headers)
        posts += 1
        return httpx.Response(400, json={"status": "fail", "private": "secret", **payload})

    async with Instagram(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(error) as caught:
            await client.auth.login_legacy("synthetic_user", "synthetic-password")
        assert "secret" not in str(caught.value)
        assert not client.session.authenticated
        if isinstance(caught.value, ChallengeRequired):
            assert caught.value.challenge_url is None
    assert posts == 1


async def test_bootstrap_rate_limit_wins_even_with_encryption_headers(key_headers):
    def server(request):
        assert request.method == "GET"
        return httpx.Response(429, headers={**key_headers, "retry-after": "120"})

    async with Instagram(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(RateLimited) as caught:
            await client.auth.login_legacy("synthetic_user", "synthetic-password")
        assert caught.value.retry_after == 120


async def test_login_user_without_credential_is_not_authenticated(key_headers):
    def server(request):
        if request.method == "GET":
            return httpx.Response(405, headers=key_headers)
        return httpx.Response(200, json={"logged_in_user": USER})

    async with Instagram(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ProtocolError):
            await client.auth.login_legacy("synthetic_user", "synthetic-password")
        assert not client.session.authenticated
