import json
from urllib.parse import parse_qs

import httpx
import pytest
from test_caa import screen_data, success_data

from async_instalib import (
    AuthOptions,
    ClientOptions,
    Instagram,
    ProtocolError,
    RequestPolicy,
    Session,
)
from async_instalib.auth.caa import LOGIN, OAUTH, PREPARE


async def test_network_and_response_limits_reach_httpx():
    def server(request):
        assert request.extensions["timeout"] == {"connect": 2, "read": 3, "write": 4, "pool": 5}
        return httpx.Response(200, content=b"x" * 101)

    options = ClientOptions(
        connect_timeout=2,
        read_timeout=3,
        write_timeout=4,
        pool_timeout=5,
        max_response_bytes=100,
        http2=False,
        max_keepalive_connections=0,
        policy=RequestPolicy(min_interval=0),
    )
    async with Instagram(
        Session(authorization="Bearer synthetic", user_id="42"),
        options,
        httpx.MockTransport(server),
    ) as client:
        with pytest.raises(ProtocolError, match="configured byte limit"):
            await client.account.me(edit=False)


async def test_auth_overrides_reach_wire_without_changing_fresh_context(key_headers):
    auth = AuthOptions(
        styles_id="synthetic-style",
        theme="synthetic-theme",
        design_system="synthetic-design",
        offline_experiment_group="synthetic-group",
        latency_marker_id=123,
        prepare_parameters={"show_internal_settings": True},
        client_parameters={"event_step": "custom-step"},
        server_parameters={"login_source": "custom-source"},
        network_info={"sim_count": 0},
    )

    def server(request):
        path = request.url.path
        if path.endswith("attestation/create_android_keystore/"):
            return httpx.Response(200, json={"challenge_nonce": "synthetic"})
        if path.endswith("qe/sync/"):
            return httpx.Response(405, headers=key_headers)
        form = parse_qs(request.content.decode())
        params = json.loads(form["params"][0])
        context = json.loads(form["bk_client_context"][0])
        assert context["styles_id"] == "synthetic-style"
        assert context["theme_params"][0]["value"] == ["synthetic-theme"]
        if path.endswith(PREPARE + "/"):
            assert params["offline_experiment_group"] == "synthetic-group"
            assert params["show_internal_settings"] is True
            return httpx.Response(200, json=screen_data())
        assert params["client_input_params"]["si_device_param_network_info"] == {"sim_count": 0}
        if path.endswith(OAUTH + "/"):
            return httpx.Response(200, json={"status": "ok"})
        assert path.endswith(LOGIN + "/")
        assert params["client_input_params"]["event_step"] == "custom-step"
        assert params["client_input_params"]["aac"] == "synthetic-server-context"
        assert params["server_params"]["login_source"] == "custom-source"
        assert params["server_params"]["INTERNAL__latency_qpl_marker_id"] == 123
        return httpx.Response(200, json=success_data())

    async with Instagram(
        options=ClientOptions(auth=auth, policy=RequestPolicy(min_interval=0)),
        transport=httpx.MockTransport(server),
    ) as client:
        auth.client_parameters["event_step"] = "mutated-after-construction"
        assert (await client.auth.login("synthetic", "synthetic-password")).id == "42"


@pytest.mark.parametrize("field", ["password", "aac", "device_id", "username_text_input_id"])
def test_protocol_state_cannot_be_accidentally_replaced(field):
    with pytest.raises(ValueError):
        AuthOptions(client_parameters={field: "stale"})
