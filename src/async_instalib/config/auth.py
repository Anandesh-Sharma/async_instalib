"""Explicit configuration for captured authentication metadata."""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthOptions:
    """Customize CAA metadata without replacing fresh credentials or server context.

    Override dictionaries are copied by the client at construction. Values must
    be JSON serializable. They merge at the top level of the corresponding map.
    """

    offline_experiment_group: str = "caa_iteration_v3_perf_ig_4"
    styles_id: str = "instagram"
    theme: str = "three_neutral_gray"
    design_system: str = "XMDS"
    latency_marker_id: int = 36707139
    trust_this_device: bool = False
    prepare_parameters: dict[str, Any] = field(default_factory=dict, repr=False)
    client_parameters: dict[str, Any] = field(default_factory=dict, repr=False)
    server_parameters: dict[str, Any] = field(default_factory=dict, repr=False)
    network_info: dict[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        reserved = {
            "password",
            "enc_password",
            "username",
            "username_input",
            "contact_point",
            "aac",
            "device_id",
            "family_device_id",
            "qe_device_id",
            "machine_id",
            "waterfall_id",
            "username_text_input_id",
            "password_text_input_id",
            "challenge_nonce",
        }
        for mapping in (self.prepare_parameters, self.client_parameters, self.server_parameters):
            if not isinstance(mapping, dict) or not all(isinstance(k, str) for k in mapping):
                raise ValueError("Authentication parameter overrides must be string-keyed objects.")
            if reserved.intersection(mapping):
                raise ValueError("Configure identity via Session; credentials/context are managed.")
        try:
            json.dumps(
                [
                    self.prepare_parameters,
                    self.client_parameters,
                    self.server_parameters,
                    self.network_info,
                ],
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ValueError("Authentication overrides must contain finite JSON values.") from None
        if self.network_info is not None and not isinstance(self.network_info, dict):
            raise ValueError("network_info must be an object or None.")
