"""Read, write, and moderate comments with the requests recorded from the app's comment sheet.

Writes are sent once with a fresh idempotence token; nothing is retried.
"""

import json
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from ..config.comments import CommentListOptions
from ..exceptions import APIError, LoginRequired, ProtocolError
from ..models.comments import Comment
from ..models.pagination import Continuation, Page, PageContext
from ..models.people import PersonSummary
from ..models.uploads import Photo
from ..transport import Transport
from ..transport.graphql import GraphQLRequest
from .comment_settings import CommentSettings
from .media_uploads import Destination, MediaUploads

# The recorded comment sheet context; the app sends it on every comment write.
_MODULE = "self_comments_v2_feed_contextual_self_profile"


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _number(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not int(value):
        raise ValueError(f"{name} must be a positive numeric ASCII string.")
    return str(int(value))


class Comments:
    """Comments on any post the signed-in account can see; moderation on its own posts."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._uploads = MediaUploads(transport)
        self.settings = CommentSettings(transport)

    async def list(self, media_id: str, options: CommentListOptions | None = None) -> Page[Comment]:
        """Read one page of top-level comments; replies come from ``replies()``."""
        settings = CommentListOptions() if options is None else options
        if not isinstance(settings, CommentListOptions):
            raise ValueError("options must be CommentListOptions.")
        media = self._media_id(media_id)
        context = PageContext("comments", self._account(), media, settings.sort)
        query = {"should_fetch_creator_comment_nudge": "true"}
        if settings.cursor is not None:
            settings.cursor.validate_for(context)
            query["min_id"] = settings.cursor.value
        if settings.sort != "default":
            query["sort_order"] = settings.sort
        query.update(can_support_threading="true", is_carousel_bumped_post="false")
        query["feed_position"] = "0"
        records = await self._transport.request_stream(
            "GET", f"media/{media}/stream_comments/?{urlencode(query)}"
        )
        comments = [row for record in records for row in record.get("comments") or []]
        last = records[-1] if records else {}
        cursor = last.get("next_min_id")
        continuation = None
        if last.get("has_more_headload_comments") or last.get("has_more_comments"):
            if not isinstance(cursor, str) or not cursor:
                raise ProtocolError("Comment page advertised more without its recorded cursor.")
            continuation = Continuation(cursor, context)
        return self._page(comments, continuation, last)

    async def replies(self, media_id: str, comment_id: str) -> Page[Comment]:
        """Read the replies to one comment. Longer threads raise until paging is recorded."""
        media, parent = self._media_id(media_id), _number(comment_id, "comment_id")
        self._account()
        result = await self._transport.request(
            "GET", f"media/{media}/comments/{parent}/inline_child_comments/"
        )
        if result.get("has_more_head_child_comments") or result.get("has_more_tail_child_comments"):
            raise ProtocolError("Reply continuation has not been recorded; no page is returned.")
        return self._page(result.get("child_comments"), None, result)

    async def post(self, media_id: str, text: str) -> Comment:
        """Comment on a post; mentions and hashtags are written as plain text."""
        return await self._create(media_id, {"comment_text": self._text(text)})

    async def reply(self, media_id: str, comment_id: str, text: str) -> Comment:
        """Reply to one comment on a post."""
        fields = {
            "comment_text": self._text(text),
            "replied_to_comment_id": _number(comment_id, "comment_id"),
        }
        return await self._create(media_id, fields)

    async def post_gif(self, media_id: str, gif_id: str) -> Comment:
        """Comment with a GIPHY GIF, by its GIPHY ID (the ID in a giphy.com URL)."""
        if not isinstance(gif_id, str) or not gif_id.isalnum():
            raise ValueError("gif_id must be the alphanumeric GIPHY media ID.")
        gif = _json({"gif_media_id": gif_id, "is_sticker": False})
        return await self._create(media_id, {"comment_text": "", "gif_params": gif})

    async def post_photo(self, media_id: str, photo: Photo) -> Comment:
        """Comment with a JPEG; it is uploaded first, as the app does."""
        if not isinstance(photo, Photo):
            raise ValueError("photo must be a Photo.")
        media = self._media_id(media_id)
        self._account()
        upload_id = await self._uploads.photo(photo, Destination("comment"))
        session = self._transport.session
        fields = {
            **self._write_fields(),
            "media_id": media,
            "upload_id": upload_id,
            "comment_text": "",
            "source_type": "4",
            "audience": "default",
            "async_publish": "1",
            "publish_id": "1",
            "original_width": "0",
            "original_height": "0",
            "device_id": session.device.android_id,
            "timezone_offset": str(session.profile.timezone_offset),
        }
        result = await self._signed("media/configure_to_comment/", fields)
        return self._comment(result.get("comment"))

    async def edit(self, comment_id: str, text: str) -> Comment:
        """Replace the text of your own comment."""
        identifier = _number(comment_id, "comment_id")
        self._account()
        result = await self._transport.graphql(
            GraphQLRequest(
                "CommentEditMutation",
                {
                    "data": {
                        "comment_text": self._text(text),
                        "container_module": _MODULE,
                        "comment_id": identifier,
                    }
                },
                client_doc_id="421612756810108204149064538236",
                route="graphql_www",
                legacy_root_field="xig_comment_edit",
            )
        )
        data = result.get("data")
        payload = data.get("xig_comment_edit") if isinstance(data, dict) else None
        if not isinstance(payload, dict) or "Success" not in str(payload.get("__typename")):
            raise APIError("Instagram did not accept the comment edit.")
        return self._comment(payload.get("comment"))

    async def delete(self, media_id: str, comment_id: str) -> None:
        """Delete a comment you wrote, or any comment on your own post."""
        fields = {
            "comment_ids_to_delete": _number(comment_id, "comment_id"),
            "container_module": _MODULE,
            **self._account_fields(),
        }
        await self._signed(f"media/{self._media_id(media_id)}/comment/bulk_delete/", fields)

    async def like(self, comment_id: str) -> None:
        """Like one comment."""
        await self._like(comment_id, "comment_like")

    async def unlike(self, comment_id: str) -> None:
        """Remove your like from one comment."""
        await self._like(comment_id, "comment_unlike")

    async def pin(self, media_id: str, comment_id: str) -> None:
        """Pin a comment to the top of your own post."""
        await self._pin(media_id, comment_id, "pin_comment")

    async def unpin(self, media_id: str, comment_id: str) -> None:
        """Unpin a comment from your own post."""
        await self._pin(media_id, comment_id, "unpin_comment")

    async def likers(self, comment_id: str) -> Page[PersonSummary]:
        """Read the people who liked one comment, as one page."""
        identifier = _number(comment_id, "comment_id")
        self._account()
        query = urlencode({"search_surface": "comment_likers_page", "filter_dismissed_users": "0"})
        result = await self._transport.request("GET", f"media/{identifier}/comment_likers/?{query}")
        users = result.get("users")
        if not isinstance(users, list):
            raise ProtocolError("Comment likers response did not contain people.")
        try:
            return Page(tuple(PersonSummary.from_dict(user) for user in users), raw=result)
        except ValueError:
            raise ProtocolError("Comment likers response contained invalid people.") from None

    async def _create(self, media_id: str, fields: dict[str, str]) -> Comment:
        media = self._media_id(media_id)
        self._account()
        result = await self._signed(f"media/{media}/comment/", {**self._write_fields(), **fields})
        return self._comment(result.get("comment"))

    async def _like(self, comment_id: str, action: str) -> None:
        identifier = _number(comment_id, "comment_id")
        fields = {
            **self._account_fields(),
            "delivery_class": "organic",
            "container_module": _MODULE,
            "is_reply_highlight": "false",
            "is_feed_preview_comment": "false",
            "is_carousel_bumped_post": "false",
            "feed_position": "0",
        }
        if action == "comment_unlike":
            fields["m_ix"] = "0"
        await self._signed(f"media/{identifier}/{action}/", fields)

    async def _pin(self, media_id: str, comment_id: str, action: str) -> None:
        media, identifier = self._media_id(media_id), _number(comment_id, "comment_id")
        await self._signed(f"media/{media}/{action}/{identifier}/", self._account_fields())

    def _write_fields(self) -> dict[str, str]:
        token = str(uuid4())
        return {
            **self._account_fields(),
            "comment_creation_key": token,
            "idempotence_token": token,
            "delivery_class": "organic",
            "radio_type": "wifi-none",
            "container_module": _MODULE,
            "carousel_index": "-1",
            "carousel_child_mentions": "[]",
            "include_carousel_child_mentions": "false",
            "is_from_carousel_child_thread": "false",
            "is_carousel_bumped_post": "false",
            "feed_position": "0",
        }

    async def _signed(self, endpoint: str, fields: dict[str, Any]) -> dict[str, Any]:
        result = await self._transport.request(
            "POST", endpoint, {"signed_body": "SIGNATURE." + _json(fields)}
        )
        if result.get("status") != "ok":
            raise ProtocolError("Comment change did not confirm successful status.")
        return result

    def _account_fields(self) -> dict[str, str]:
        return {"_uid": self._account(), "_uuid": self._transport.session.device.id}

    def _account(self) -> str:
        session = self._transport.session
        if not session.authenticated or not session.user_id:
            raise LoginRequired("Load your own authenticated session before using comments.")
        return session.user_id

    def _media_id(self, value: str) -> str:
        """Return ``<pk>_<owner>``; a bare pk names a post of the signed-in account."""
        parts = value.split("_") if isinstance(value, str) else []
        if not 1 <= len(parts) <= 2:
            raise ValueError("media_id must be <pk> or <pk>_<owner id>.")
        numbers = [_number(part, "media_id") for part in parts]
        owner = numbers[1] if len(numbers) == 2 else self._account()
        return f"{numbers[0]}_{owner}"

    @staticmethod
    def _text(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Comment text must be nonempty.")
        return value

    @staticmethod
    def _comment(value: object) -> Comment:
        try:
            if not isinstance(value, dict):
                raise ValueError
            return Comment.from_dict(value)
        except ValueError:
            raise ProtocolError("Response did not contain the comment.") from None

    @staticmethod
    def _page(
        rows: object, continuation: Continuation | None, raw: dict[str, Any]
    ) -> Page[Comment]:
        if not isinstance(rows, list):
            raise ProtocolError("Comment response did not contain comments.")
        try:
            items = tuple(Comment.from_dict(row) for row in rows)
        except ValueError:
            raise ProtocolError("Comment response contained an invalid comment.") from None
        total = raw.get("comment_count", raw.get("child_comment_count"))
        return Page(items, continuation, total if type(total) is int else None, raw)
