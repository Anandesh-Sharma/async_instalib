"""Create, change, and delete your story highlights with the recorded requests."""

import json
import time
from typing import Any

from ..config.posts import HighlightChanges
from ..exceptions import LoginRequired, ProtocolError
from ..models.stories import Highlight
from ..transport import Transport
from .media_actions import own_media_id

# The app's default cover crop: a centred square of a 9:16 story.
_SQUARE_CROP = "[0.0,0.21875,1.0,0.78125]"


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


class Highlights:
    """Story highlights of the signed-in account; reading them is ``people.content``."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def create(self, title: str, story_ids: list[str]) -> Highlight:
        """Create a highlight from your stories; the first story becomes the cover."""
        changes = HighlightChanges(title=title, add=tuple(story_ids))
        if changes.title is None or not changes.add:
            raise ValueError("A highlight needs a title and at least one story.")
        stories = [self._media_id(item) for item in changes.add]
        fields = {
            **self._account_fields(),
            "source": "STORY_VIEWER_PROFILE",
            "should_add_to_main_grid": "false",
            "creation_id": str(time.time_ns() // 1_000_000),
            "title": changes.title,
            "media_ids": _json(stories),
            "cover": _json({"media_id": stories[0], "crop_rect": _SQUARE_CROP}),
        }
        result = await self._signed("highlights/create_reel/", fields)
        return self._highlight(result)

    async def edit(self, highlight: Highlight, changes: HighlightChanges) -> Highlight:
        """Rename a highlight or add and remove stories, keeping its cover."""
        if not isinstance(highlight, Highlight) or not isinstance(changes, HighlightChanges):
            raise ValueError("edit takes a Highlight and HighlightChanges.")
        cover = highlight.cover.raw if highlight.cover is not None else {}
        cover_id, crop = cover.get("media_id"), cover.get("crop_rect")
        if not isinstance(cover_id, str) or not isinstance(crop, list):
            raise ValueError("Highlight lacks its cover; read it from the highlight tray.")
        fields = {
            **self._account_fields(),
            "source": "SELF_PROFILE",
            "title": changes.title if changes.title is not None else highlight.title or "",
            "added_media_ids": _json([self._media_id(item) for item in changes.add]),
            "removed_media_ids": _json([self._media_id(item) for item in changes.remove]),
            "cover": _json({"media_id": cover_id, "crop_rect": _json(crop)}),
        }
        result = await self._signed(f"highlights/{highlight.id}/edit_reel/", fields)
        return self._highlight(result)

    async def delete(self, highlight_id: str) -> None:
        """Delete a highlight; its stories stay in your story archive."""
        if not isinstance(highlight_id, str) or not highlight_id.startswith("highlight:"):
            raise ValueError("highlight_id must be the highlight:<id> value.")
        if not highlight_id.removeprefix("highlight:").isdigit():
            raise ValueError("highlight_id must be the highlight:<id> value.")
        await self._signed(f"highlights/{highlight_id}/delete_reel/", self._account_fields())

    async def _signed(self, endpoint: str, fields: dict[str, Any]) -> dict[str, Any]:
        result = await self._transport.request(
            "POST", endpoint, {"signed_body": "SIGNATURE." + _json(fields)}
        )
        if result.get("status") != "ok":
            raise ProtocolError("Highlight change did not confirm successful status.")
        return result

    @staticmethod
    def _highlight(result: dict[str, Any]) -> Highlight:
        try:
            reel = result["reel"]
            if not isinstance(reel, dict):
                raise ValueError
            return Highlight.from_dict(reel)
        except (KeyError, ValueError):
            raise ProtocolError("Response did not contain the highlight.") from None

    def _account_fields(self) -> dict[str, str]:
        session = self._transport.session
        if not session.authenticated or not session.user_id:
            raise LoginRequired("Load your own authenticated session before changing highlights.")
        return {"_uid": session.user_id, "_uuid": session.device.id}

    def _media_id(self, value: str) -> str:
        return own_media_id(value, self._account_fields()["_uid"])
