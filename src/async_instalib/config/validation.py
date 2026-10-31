"""Shared configuration validation."""


def validate_header_value(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) > 126 for character in value)
    ):
        raise ValueError("Header values must be nonempty printable ASCII without edge whitespace.")
