"""CAA password flow derived from the Android 452.1 login capture."""

import json
import time
from typing import Any
from uuid import uuid4

import httpx

from ..exceptions import LoginFlowUnsupported, ProtocolError
from ..transport import APIResponse, RequestData, Transport
from . import bloks
from .password import PasswordKey

PREPARE = "com.bloks.www.bloks.caa.login.process_client_data_and_redirect"
OAUTH = "com.bloks.www.caa.login.oauth.token.fetch.async"
LOGIN = "com.bloks.www.bloks.caa.login.async.send_login_request"


class CAALogin:
    """Obtain fresh server context, then submit one encrypted password exchange."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._options = transport.auth_options

    async def login(self, username: str, password: str) -> APIResponse:
        session = self._transport.session
        device = session.device
        waterfall_id = str(uuid4())
        nonce = await self._transport.request(
            "POST",
            "attestation/create_android_keystore/",
            {"app_scoped_device_id": device.id, "key_hash": ""},
        )
        challenge_nonce = nonce.get("challenge_nonce")
        if not isinstance(challenge_nonce, str) or not challenge_nonce:
            raise ProtocolError("Instagram did not supply a device challenge nonce.")
        screen = await self._action(
            PREPARE,
            {
                "is_from_logged_out": False,
                "logged_out_user": "",
                "qpl_join_id": None,
                "family_device_id": device.family_id,
                "device_id": device.android_id,
                "offline_experiment_group": self._options.offline_experiment_group,
                "waterfall_id": waterfall_id,
                "logout_source": "",
                "show_internal_settings": False,
                "last_auto_login_time": 0,
                "disable_auto_login": False,
                "qe_device_id": device.id,
                "use_auto_login_interstitial": True,
                "disable_recursive_auto_login_interstitial": True,
                "auto_login_interstitial_experiment_group_name": "",
                "is_from_logged_in_switcher": False,
                "switcher_logged_in_uid": "",
                "account_list": [],
                "blocked_uid": [],
                "INTERNAL_INFRA_THEME": "THREE_NEUTRAL_GRAY",
                "layered_homepage_experiment_group": "Deploy: Not in Experiment",
                "launched_url": "",
                "sim_phone_numbers": [],
                "is_from_registration_reminder": False,
            },
        )
        context = bloks.account_access_context(screen)
        server = bloks.server_parameters(screen, LOGIN)
        if not all(server.get(k) for k in ("username_text_input_id", "password_text_input_id")):
            raise LoginFlowUnsupported("Instagram did not supply login input identifiers.")
        server.update(self._options.server_parameters)
        oauth_server: dict[str, Any] = {
            key: server[key]
            for key in (
                "is_from_logged_out",
                "layered_homepage_experiment_group",
                "device_id",
                "login_surface",
                "waterfall_id",
                "is_platform_login",
                "login_entry_point",
                "family_device_id",
                "offline_experiment_group",
                "access_flow_version",
                "is_from_logged_in_switcher",
                "qe_device_id",
            )
            if key in server
        }
        self._latency(oauth_server)
        await self._action(
            OAUTH,
            {
                "client_input_params": {
                    "username_input": username,
                    "aac": context,
                    "lois_settings": {"lois_token": ""},
                    "cloud_trust_token": None,
                    "zero_balance_state": "",
                    "si_device_param_network_info": self._network_info(),
                },
                "server_params": oauth_server,
            },
        )
        bootstrap = await self._transport.send("GET", "qe/sync/")
        if bootstrap.status_code not in {200, 405}:
            bootstrap.raise_for_error(bootstrap.json())
        try:
            key = PasswordKey(
                int(bootstrap.headers["ig-set-password-encryption-key-id"]),
                bootstrap.headers["ig-set-password-encryption-pub-key"],
            )
        except (KeyError, ValueError):
            raise ProtocolError(
                "Instagram did not supply valid password encryption keys."
            ) from None
        client: dict[str, Any] = {
            "blocked_uids": [],
            "aac": context,
            "sim_phones": [],
            "aymh_accounts": [],
            "secure_family_device_id": "",
            "has_granted_read_contacts_permissions": 0,
            "auth_secure_device_id": "",
            "has_whatsapp_installed": 0,
            "si_device_param_network_info": self._network_info(),
            "password": key.encrypt(password),
            "sso_token_map_json_string": "",
            "block_store_machine_id": "",
            "ig_vetted_device_nonces": None,
            "cloud_trust_token": None,
            "event_flow": "login_manual",
            "password_contains_non_ascii": str(not password.isascii()).lower(),
            "client_known_key_hash": "",
            "sso_accounts_auth_data": [],
            "encrypted_msisdn": "",
            "has_granted_read_phone_permissions": 0,
            "app_manager_id": "",
            "should_show_nested_nta_from_aymh": 0,
            "device_id": device.android_id,
            "zero_balance_state": "",
            "login_attempt_count": 1,
            "machine_id": session.mid or "",
            "flash_call_permission_status": dict.fromkeys(
                ("READ_PHONE_STATE", "READ_CALL_LOG", "ANSWER_PHONE_CALLS"), "DENIED"
            ),
            "accounts_list": [],
            "gms_incoming_call_retriever_eligibility": "not_eligible",
            "family_device_id": device.family_id,
            "fb_ig_device_id": [],
            "device_emails": [],
            "try_num": 1,
            "lois_settings": {"lois_token": ""},
            "event_step": "home_page",
            "headers_infra_flow_id": "",
            "openid_tokens": {},
            "contact_point": username,
        }
        client.update(self._options.client_parameters)
        self._latency(server)
        server["is_from_empty_password"] = 0
        # The captured emulator reports unavailable Android keystore (-1013). Python also
        # lacks that keystore; no certificate, signed nonce, or hardware claim is fabricated.
        attest = json.dumps(
            {
                "attestation": [
                    {
                        "version": 2,
                        "type": "keystore",
                        "errors": [-1013],
                        "challenge_nonce": challenge_nonce,
                        "signed_nonce": "",
                        "key_hash": "",
                    }
                ]
            },
            separators=(",", ":"),
        )
        payload = await self._action(
            LOGIN,
            {"client_input_params": client, "server_params": server},
            {"X-IG-Attest-Params": attest},
        )
        parsed = bloks.login_result(payload)
        # Embedded CAA headers are JSON values; the live key-id header can be an integer.
        # Normalize scalar values before handing them to HTTPX's wire-header container.
        if any(
            not isinstance(k, str) or type(v) not in (str, int)
            for k, v in parsed["headers"].items()
        ):
            raise ProtocolError("Instagram returned an unsupported embedded header value.")
        try:
            headers = httpx.Headers({k: str(v) for k, v in parsed["headers"].items()})
        except (ValueError, UnicodeError):
            raise ProtocolError("Instagram returned an invalid embedded header.") from None
        self._transport.receive_state(headers)
        return APIResponse(
            200, headers, self._endpoint(LOGIN), json.dumps(parsed["result"]).encode()
        )

    async def _action(
        self, name: str, params: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        version = self._transport.session.profile.bloks_version
        if name == PREPARE:
            params = {**params, **self._options.prepare_parameters}
        form = {
            "params": json.dumps(params, separators=(",", ":")),
            "bloks_versioning_id": version,
            "bk_client_context": json.dumps(
                {
                    "bloks_version": version,
                    "styles_id": self._options.styles_id,
                    "theme_params": [
                        {
                            "value": [self._options.theme],
                            "design_system_name": self._options.design_system,
                        }
                    ],
                },
                separators=(",", ":"),
            ),
        }
        response = await self._transport.send(
            "POST", self._endpoint(name), RequestData(form, headers or {})
        )
        result = response.json()
        response.raise_for_error(result)
        return result

    @staticmethod
    def _endpoint(name: str) -> str:
        return f"bloks/async_action/{name}/"

    def _latency(self, params: dict[str, Any]) -> None:
        params["INTERNAL__latency_qpl_marker_id"] = self._options.latency_marker_id
        params["INTERNAL__latency_qpl_instance_id"] = int(time.time() * 1000)

    def _network_info(self) -> dict[str, Any]:
        if self._options.network_info is not None:
            return self._options.network_info
        # A Python process has no Android telephony subscriptions to report.
        return {
            "active_subscriptions_info": None,
            "default_subscription_info": None,
            "active_data_subscription_info": None,
            "default_sms_subscription_info": None,
            "is_airplane_mode": 0,
            "is_active_network_cellular": 0,
            "is_device_sms_capable": 0,
            "sim_count": 0,
            "is_wifi": 1,
        }
