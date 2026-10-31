"""One-highlight request serialization and progressive reel-map handling."""

import json
from typing import Any

from ..config.metadata import HighlightItemsOptions
from ..exceptions import LoginRequired, ProtocolError
from ..models.stories import HighlightItems, _highlight_id
from ..transport import Transport


class HighlightReader:
    """Read a selected highlight without automatic neighboring prefetch or seen calls."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def items(
        self, highlight_id: str, options: HighlightItemsOptions | None = None
    ) -> HighlightItems:
        identifier = _highlight_id(highlight_id)
        if options is not None and not isinstance(options, HighlightItemsOptions):
            raise ValueError("options must be HighlightItemsOptions or None.")
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Load an authenticated session before reading highlight items.")
        settings = options or HighlightItemsOptions()
        fields: dict[str, Any] = {
            "exclude_media_ids": json.dumps(settings.exclude_media_ids, separators=(",", ":")),
            "reason": settings.reason,
            "source": settings.source,
            "batch_size": str(settings.batch_size),
            "_uid": session.user_id,
            "_uuid": session.device.id,
            "reel_ids": [identifier],
        }
        if settings.supported_capabilities_new is not None:
            fields["supported_capabilities_new"] = settings.supported_capabilities_new
        records = await self._transport.request_stream(
            "POST",
            "feed/reels_media_stream/",
            {
                "signed_body": "SIGNATURE."
                + json.dumps(fields, separators=(",", ":"), allow_nan=False)
            },
        )
        selected: dict[str, Any] | None = None
        for record in records:
            reels = record.get("reels")
            if not isinstance(reels, dict) or any(key != identifier for key in reels):
                raise ProtocolError("Highlight stream contained an unexpected reel map.")
            if identifier in reels:
                if selected is not None or not isinstance(reels[identifier], dict):
                    raise ProtocolError("Highlight stream repeated or malformed the selected reel.")
                selected = reels[identifier]
        if selected is None:
            raise ProtocolError("Highlight stream did not return the requested highlight.")
        try:
            result = HighlightItems.from_dict(selected)
            if result.id != identifier:
                raise ValueError
            return result
        except ValueError:
            raise ProtocolError("Highlight stream contained invalid item metadata.") from None
