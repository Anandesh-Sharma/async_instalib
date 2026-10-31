"""Bounds for inert muted-screen metadata inspection."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MutedDecodeOptions:
    """Limits count decoded JSON and inspected expression literals cumulatively."""

    max_decoded_bytes: int = 16 * 1024 * 1024
    max_nodes: int = 100_000
    max_depth: int = 64
    max_embedded_payloads: int = 64

    def __post_init__(self) -> None:
        for value in (
            self.max_decoded_bytes,
            self.max_nodes,
            self.max_depth,
            self.max_embedded_payloads,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("Muted metadata decoding limits must be positive integers.")
