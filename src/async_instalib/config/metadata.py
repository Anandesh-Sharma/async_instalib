"""Bounds for decoding the About screen's embedded JSON bundle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AboutDecodeOptions:
    """Limits apply to inert JSON inspection; Bloks programs are never executed."""

    max_bundle_bytes: int = 16 * 1024 * 1024
    max_nodes: int = 100_000
    max_depth: int = 64

    def __post_init__(self) -> None:
        for value in (self.max_bundle_bytes, self.max_nodes, self.max_depth):
            if type(value) is not int or value < 1:
                raise ValueError("About decoding limits must be positive integers.")


@dataclass(frozen=True, slots=True)
class HighlightItemsOptions:
    """Context for one explicit highlight read; no playback or acknowledgement."""

    reason: str = "on_tap"
    source: str = "profile"
    batch_size: int = 1
    exclude_media_ids: tuple[str, ...] = ()
    supported_capabilities_new: str | None = None

    def __post_init__(self) -> None:
        for value in (self.reason, self.source):
            if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
                raise ValueError("Highlight request context must be nonempty text.")
        if type(self.batch_size) is not int or self.batch_size < 1:
            raise ValueError("batch_size must be a positive integer.")
        if not isinstance(self.exclude_media_ids, (list, tuple)) or any(
            not isinstance(value, str)
            or not value
            or any(not part.isascii() or not part.isdigit() for part in value.split("_"))
            for value in self.exclude_media_ids
        ):
            raise ValueError("Excluded media IDs must be numeric or compound numeric strings.")
        if self.supported_capabilities_new is not None and not isinstance(
            self.supported_capabilities_new, str
        ):
            raise ValueError("supported_capabilities_new must be a string or None.")
        object.__setattr__(self, "exclude_media_ids", tuple(self.exclude_media_ids))
