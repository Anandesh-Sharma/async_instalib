"""Manage your published media with the requests recorded from each app menu action."""

import json
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from ..config.posts import EditOptions
from ..exceptions import LoginRequired, ProtocolError
from ..models.content import Media
from ..models.pagination import Page
from ..transport import Transport

# Delete requests name the media type, except reels; stories also send a deletion ID.
_DELETE_TYPES = {1: "PHOTO", 2: "VIDEO", 8: "CAROUSEL"}


class MediaActions:
    """One change per call to a post, reel, or story owned by the signed-in account.

    Methods accept the media ID from a ``Publication`` or ``Media`` (``<pk>`` or
    ``<pk>_<user id>``). Nothing is retried; a network error leaves the result unknown.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def edit(self, media_id: str, options: EditOptions) -> Media:
        """Replace the caption and optionally set alt text, location, or added tags."""
        if not isinstance(options, EditOptions):
            raise ValueError("options must be EditOptions.")
        identifier = self._media_id(media_id)
        fields: dict[str, Any] = {
            **self._account_fields(),
            "device_id": self._transport.session.device.android_id,
            "caption_text": options.caption,
            "container_module": "edit_media_info",
        }
        if options.alt_text is not None:
            fields["custom_accessibility_caption"] = options.alt_text
        if options.location is not None:
            fields["location"] = options.location.location_field()
        if options.user_tags:
            people = [
                {"user_id": int(tag.user_id), "position": [tag.x, tag.y]}
                for tag in options.user_tags
            ]
            added = [tag.user_id for tag in options.user_tags]
            fields["usertags"] = _json({"in": people, "added": added})
        result = await self._signed(f"media/{identifier}/edit_media/", fields)
        return self._media(result.get("media"))

    async def delete(self, media: Media) -> None:
        """Delete a post, carousel, reel, or story permanently.

        Takes the ``Media`` from a publication or profile read, because the request
        differs by kind. Instagram may keep a deleted post in Recently Deleted.
        """
        if not isinstance(media, Media):
            raise ValueError("delete takes the Media to remove.")
        identifier = self._media_id(media.id)
        fields = {"media_id": identifier, **self._account_fields()}
        endpoint = f"media/{identifier}/delete/"
        if media.product_type != "clips":
            kind = _DELETE_TYPES.get(media.media_type or 0)
            if kind is None or (media.product_type is None and kind != "CAROUSEL"):
                raise ValueError("Media lacks the type needed to delete it; read it again.")
            endpoint += "?" + urlencode({"media_type": kind})
            if media.product_type == "story":
                fields["deep_delete_waterfall"] = str(uuid4())
            else:
                fields["igtv_feed_preview"] = "false"
        result = await self._signed(endpoint, fields)
        if result.get("did_delete") is not True:
            raise ProtocolError("Instagram did not confirm the deletion.")

    async def archive(self, media_id: str) -> None:
        """Hide a post from your profile; only you can see it in your archive."""
        await self._visibility(media_id, "only_me")

    async def unarchive(self, media_id: str) -> None:
        """Show an archived post on your profile again."""
        await self._visibility(media_id, "undo_only_me")

    async def archived(self) -> Page[Media]:
        """Read your archived posts. Larger archives raise until paging is recorded."""
        self._require_session()
        result = await self._transport.request("GET", "feed/only_me_feed/")
        items = result.get("items")
        if not isinstance(items, list):
            raise ProtocolError("Archive response did not contain items.")
        if result.get("more_available") or result.get("max_id"):
            raise ProtocolError("Archive continuation has not been recorded; no page is returned.")
        try:
            return Page(tuple(Media.from_dict(item) for item in items), raw=result)
        except ValueError:
            raise ProtocolError("Archive response contained invalid media.") from None

    async def pin(self, media_id: str) -> None:
        """Pin a post to the top of your profile grid."""
        await self._grid_form("users/pin_timeline_media/", "post_id", media_id)

    async def unpin(self, media_id: str) -> None:
        """Unpin a post from the top of your profile grid."""
        await self._grid_form("users/unpin_timeline_media/", "post_id", media_id)

    async def add_to_grid(self, media_id: str) -> None:
        """Show a reel in your profile's main grid."""
        result = await self._grid_form("feed/profile_grid/add/", "media_id", media_id)
        self._check_grid(result, True)

    async def remove_from_grid(self, media_id: str) -> None:
        """Keep a reel in the Reels tab only, removing it from the main grid."""
        result = await self._grid_form("feed/profile_grid/remove/", "media_id", media_id)
        self._check_grid(result, False)

    async def enable_comments(self, media_id: str) -> None:
        """Turn commenting on for a post or story."""
        await self._form(f"media/{self._media_id(media_id)}/enable_comments/", {})

    async def disable_comments(self, media_id: str) -> None:
        """Turn commenting off for a post or story."""
        await self._form(f"media/{self._media_id(media_id)}/disable_comments/", {})

    async def set_counts(self, media_id: str, hide_likes: bool, hide_shares: bool) -> None:
        """Hide or show a post's like and share counts; both values are always sent."""
        if type(hide_likes) is not bool or type(hide_shares) is not bool:
            raise ValueError("hide_likes and hide_shares must be booleans.")
        await self._form(
            "media/update_like_and_view_counts_visibility/",
            {
                "like_and_view_counts_disabled": str(hide_likes).lower(),
                "share_count_disabled": str(hide_shares).lower(),
                "media_id": self._media_id(media_id),
            },
        )

    async def _visibility(self, media_id: str, action: str) -> None:
        identifier = self._media_id(media_id)
        await self._signed(
            f"media/{identifier}/{action}/", {"media_id": identifier, **self._account_fields()}
        )

    async def _grid_form(self, endpoint: str, name: str, media_id: str) -> dict[str, Any]:
        # Grid and pin requests name the bare media number rather than <pk>_<user id>.
        return await self._form(endpoint, {name: self._media_id(media_id).split("_")[0]})

    @staticmethod
    def _check_grid(result: dict[str, Any], expected: bool) -> None:
        media = result.get("media")
        if not isinstance(media, dict) or media.get("is_in_profile_grid") is not expected:
            raise ProtocolError("Instagram did not confirm the profile grid change.")

    async def _signed(self, endpoint: str, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._post(endpoint, {"signed_body": "SIGNATURE." + _json(fields)})

    async def _form(self, endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
        return await self._post(endpoint, {**fields, "_uuid": self._transport.session.device.id})

    async def _post(self, endpoint: str, form: dict[str, str]) -> dict[str, Any]:
        result = await self._transport.request("POST", endpoint, form)
        if result.get("status") != "ok":
            raise ProtocolError("Media change did not confirm successful status.")
        return result

    def _account_fields(self) -> dict[str, str]:
        session = self._transport.session
        return {"_uid": session.user_id or "", "_uuid": session.device.id}

    def _media_id(self, value: str) -> str:
        self._require_session()
        return own_media_id(value, self._transport.session.user_id or "")

    @staticmethod
    def _media(value: object) -> Media:
        try:
            if not isinstance(value, dict):
                raise ValueError
            return Media.from_dict(value)
        except ValueError:
            raise ProtocolError("Response did not contain the edited media.") from None

    def _require_session(self) -> None:
        if not self._transport.session.authenticated or not self._transport.session.user_id:
            raise LoginRequired("Load your own authenticated session before changing media.")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def own_media_id(value: str, owner: str) -> str:
    """Return ``<pk>_<owner>`` for the signed-in account's media; a bare pk is accepted."""
    parts = value.split("_") if isinstance(value, str) else []
    if not 1 <= len(parts) <= 2 or any(not p.isascii() or not p.isdigit() for p in parts):
        raise ValueError("Media IDs must be <pk> or <pk>_<user id>.")
    if len(parts) == 2 and parts[1] != owner:
        raise ValueError("These actions apply only to the signed-in account's media.")
    return f"{parts[0]}_{owner}"
