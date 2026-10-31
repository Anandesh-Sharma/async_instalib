import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import Instagram, LoginFlowUnsupported, ProtocolError, Session
from async_instalib.auth import bloks as _bloks
from async_instalib.auth.caa import LOGIN, OAUTH, PREPARE


def screen_data():
    server = {
        "credential_type": "password",
        "username_text_input_id": "server-fresh:101",
        "password_text_input_id": "server-fresh:102",
        "device_id": "android-synthetic",
        "family_device_id": "synthetic-family",
        "waterfall_id": "synthetic-flow",
        "is_from_empty_password": True,
        "login_entry_point": "logged_out",
    }
    keys = " ".join(json.dumps(k) for k in server)
    values = " ".join(json.dumps(v) for v in server.values())
    return {
        "status": "ok",
        "layout": {
            "bloks_payload": {
                "data": [
                    {
                        "data": {
                            "key": "CAA_ACCOUNT_ACCESS_CONTEXT:aac",
                            "initial_lispy": '(opaque "synthetic-server-context")',
                        }
                    }
                ],
                "action": f"(invoke {json.dumps(LOGIN)} (map (array {keys}) (array {values})))",
            }
        },
    }


def success_data(key_id="9"):
    embedded = json.dumps(
        {
            "login_response": json.dumps(
                {"status": "ok", "logged_in_user": {"pk": 42, "username": "synthetic_user"}}
            ),
            "headers": json.dumps(
                {
                    "IG-Set-Authorization": "Bearer synthetic-caa-token",
                    "IG-Set-Password-Encryption-Key-Id": key_id,
                    "ig-set-ig-u-rur": "synthetic-caa-route",
                }
            ),
        }
    )
    return {"layout": {"bloks_payload": {"action": f"(login {json.dumps(embedded)})"}}}


@pytest.mark.parametrize("key_id", ["9", 9])
async def test_caa_uses_fresh_server_context_and_embedded_credentials(
    key_headers, tmp_path, key_id
):
    calls = []

    def server(request):
        path = request.url.path
        calls.append(path)
        if path.endswith("attestation/create_android_keystore/"):
            return httpx.Response(200, json={"challenge_nonce": "synthetic-nonce", "status": "ok"})
        if path.endswith("qe/sync/"):
            return httpx.Response(405, headers=key_headers)
        if path.endswith("accounts/current_user/"):
            assert request.headers["authorization"] == "Bearer synthetic-caa-token"
            assert request.headers["ig-u-rur"] == "synthetic-caa-route"
            return httpx.Response(200, json={"user": {"pk": 42, "username": "synthetic_user"}})
        form = parse_qs(request.content.decode())
        params = json.loads(form["params"][0])
        if path.endswith(PREPARE + "/"):
            assert params["account_list"] == []
            assert not params["is_from_logged_in_switcher"]
            return httpx.Response(200, json=screen_data())
        if path.endswith(OAUTH + "/"):
            assert params["client_input_params"]["aac"] == "synthetic-server-context"
            assert params["client_input_params"]["username_input"] == "synthetic_user"
            return httpx.Response(200, json={"status": "ok"})
        assert path.endswith(LOGIN + "/")
        assert params["server_params"]["username_text_input_id"] == "server-fresh:101"
        assert params["server_params"]["password_text_input_id"] == "server-fresh:102"
        assert params["server_params"]["is_from_empty_password"] == 0
        assert params["client_input_params"]["password"].startswith("#PWD_INSTAGRAM:4:")
        assert params["client_input_params"]["accounts_list"] == []
        attestation = json.loads(request.headers["x-ig-attest-params"])["attestation"][0]
        assert attestation["challenge_nonce"] == "synthetic-nonce"
        assert attestation["signed_nonce"] == ""
        assert attestation["errors"] == [-1013]
        return httpx.Response(200, json=success_data(key_id))

    async with Instagram(transport=httpx.MockTransport(server)) as client:
        assert (await client.auth.login("synthetic_user", "synthetic-password")).id == "42"
        client.session.save(tmp_path / "session.json")
    async with Instagram(
        Session.load(tmp_path / "session.json"), transport=httpx.MockTransport(server)
    ) as client:
        assert (await client.account.me()).id == "42"
    assert sum(path.endswith(LOGIN + "/") for path in calls) == 1
    assert not any(path.endswith("accounts/login/") for path in calls)


def test_server_expression_operators_are_never_executed():
    assert _bloks.expression_at('(dangerous "a\\"b" (nested true null 3))', 0) == [
        "dangerous",
        'a"b',
        ["nested", True, None, 3],
    ]
    with pytest.raises(ProtocolError):
        _bloks.expression_at("(" * 129, 0)
    with pytest.raises(ProtocolError):
        _bloks.expression_at('(incomplete "value"', 0)


def test_missing_or_dynamic_server_map_fails_without_inventing_parameters():
    with pytest.raises(LoginFlowUnsupported):
        _bloks.server_parameters({}, LOGIN)
    payload = screen_data()
    payload["layout"]["bloks_payload"]["action"] = (
        f'(call "{LOGIN}" (map (array "input_id") (array (get "dynamic"))))'
    )
    with pytest.raises(LoginFlowUnsupported):
        _bloks.server_parameters(payload, LOGIN)


def test_unsupported_verification_is_not_mistaken_for_login_success():
    with pytest.raises(LoginFlowUnsupported):
        _bloks.login_result({"layout": {"bloks_payload": {"action": '(verify "context")'}}})
