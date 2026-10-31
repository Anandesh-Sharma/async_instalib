"""Publish photos, carousels, reels, and stories with the app's recorded configure requests.

Each publish uploads the media, then sends one configure request. Nothing is retried:
if a network error interrupts either step, check the profile before publishing again.
"""

import json
import re
import time
from typing import Any

from ..config.posts import CarouselItem, PostOptions, ReelOptions, StoryOptions, UserTag
from ..exceptions import APIError, LoginRequired, ProtocolError
from ..models.posts import Publication
from ..models.uploads import Photo, Video
from ..transport import Transport
from .highlights import Highlights
from .media_actions import MediaActions
from .media_uploads import Destination, MediaUploads
from .places import Places

# Manufacturer is "samsung", or "Google/google" when the brand is written separately.
_ANDROID = re.compile(r"Android \((\d+)/([^;]+); [^;]+; [^;]+; ([^/;]+)(?:/[^;]+)?; ([^;]+);")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _tags(tags: tuple[UserTag, ...]) -> str:
    rows = [{"user_id": int(tag.user_id), "position": [tag.x, tag.y]} for tag in tags]
    return _json({"in": rows})


def _flag(value: bool) -> str:
    return "1" if value else "0"


def _photo_fields(photo: Photo) -> dict[str, Any]:
    return {
        "original_width": str(photo.width),
        "original_height": str(photo.height),
        "edits": {"filter_type": 0, "filter_strength": 1.0},
        "extra": {"source_width": photo.width, "source_height": photo.height},
    }


def _video_fields(video: Video) -> dict[str, Any]:
    seconds = video.duration_ms / 1000
    return {
        "original_width": str(video.width),
        "original_height": str(video.height),
        "length": seconds,
        "clips": [{"length": seconds, "source_type": "4"}],
        "poster_frame_index": 0,
        "audio_muted": False,
        "video_result": "",
        "edits": {"filter_type": 0, "filter_strength": 1.0},
        "extra": {"source_width": video.width, "source_height": video.height},
    }


class Posts:
    """Publish to the signed-in account and read publishing status."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._uploads = MediaUploads(transport)
        self.media = MediaActions(transport)
        self.highlights = Highlights(transport)
        self.places = Places(transport)

    async def publish_photo(self, photo: Photo, options: PostOptions | None = None) -> Publication:
        """Publish one JPEG to the feed."""
        settings = PostOptions() if options is None else options
        if not isinstance(photo, Photo) or not isinstance(settings, PostOptions):
            raise ValueError("publish_photo takes a Photo and PostOptions.")
        self._require_session()
        upload_id = await self._uploads.photo(photo, Destination("feed"))
        fields = {**self._post_fields(settings), **_photo_fields(photo), "upload_id": upload_id}
        if settings.user_tags:
            fields["usertags"] = _tags(settings.user_tags)
        if settings.alt_text is not None:
            fields["custom_accessibility_caption"] = settings.alt_text
        return await self._configure("media/configure/", fields)

    async def publish_carousel(
        self, items: list[Photo | Video | CarouselItem], options: PostOptions | None = None
    ) -> Publication:
        """Publish 2 to 10 photos and videos as one album, in the given order.

        Carousel videos need ``cover``: the app uploads a JPEG first frame for each.
        Put per-photo tags and alt text on ``CarouselItem``. Every item is checked
        before the first upload.
        """
        settings = PostOptions() if options is None else options
        if not isinstance(settings, PostOptions):
            raise ValueError("options must be PostOptions.")
        if settings.user_tags or settings.alt_text is not None:
            raise ValueError("Set carousel tags and alt text per photo with CarouselItem.")
        entries = [item if isinstance(item, CarouselItem) else CarouselItem(item) for item in items]
        if not 2 <= len(entries) <= 10:
            raise ValueError("A carousel has 2 to 10 items.")
        self._require_session()
        album_id = self._uploads.new_id()
        children = [await self._child(entry, Destination("feed", album_id)) for entry in entries]
        fields = {
            **self._post_fields(settings),
            "upload_id": album_id,
            "client_sidecar_id": album_id,
            "carousel_mixed_aspect_ratio_type": "0",
            "children_metadata": children,
        }
        return await self._configure("media/configure_sidecar/", fields)

    async def publish_reel(self, video: Video, options: ReelOptions | None = None) -> Publication:
        """Publish an MP4 as a reel; Instagram takes the cover from the video."""
        settings = ReelOptions() if options is None else options
        if not isinstance(video, Video) or not isinstance(settings, ReelOptions):
            raise ValueError("publish_reel takes a Video and ReelOptions.")
        self._require_session()
        upload_id = await self._uploads.video(video, Destination("reels"))
        fields = {
            **self._common_fields(),
            **_video_fields(video),
            "upload_id": upload_id,
            "caption": settings.caption,
            "capture_type": "clips_v2",
            "clips_share_preview_to_feed": _flag(settings.share_to_feed),
            "disable_comments": _flag(settings.comments_disabled),
            "like_and_view_counts_disabled": _flag(settings.hide_like_count),
            "share_count_disabled": _flag(settings.hide_share_count),
        }
        if settings.location is not None:
            fields["location"] = settings.location.location_field()
        if settings.tagged_user_ids:
            # Reels record people without a position: the app sends [0.0, 0.0].
            people = [{"user_id": int(i), "position": [0.0, 0.0]} for i in settings.tagged_user_ids]
            fields["usertags"] = _json({"in": people})
        return await self._configure("media/configure_to_clips/?video=1", fields)

    async def publish_story(
        self, media: Photo | Video, options: StoryOptions | None = None
    ) -> Publication:
        """Publish a photo or video story for 24 hours."""
        settings = StoryOptions() if options is None else options
        if not isinstance(media, (Photo, Video)) or not isinstance(settings, StoryOptions):
            raise ValueError("publish_story takes a Photo or Video and StoryOptions.")
        self._require_session()
        now = str(int(time.time()))
        fields = {
            **self._common_fields(),
            "configure_mode": "1",
            "capture_type": "normal",
            "creation_surface": "camera",
            "client_shared_at": now,
            "client_timestamp": now,
            "audience": "besties" if settings.close_friends else "default",
        }
        if settings.caption is not None:
            fields["comment_as_caption"] = settings.caption
        if isinstance(media, Video):
            fields.update(_video_fields(media))
            fields["upload_id"] = await self._uploads.video(media, Destination("stories"))
            return await self._configure("media/configure_to_story/?video=1", fields)
        fields.update(_photo_fields(media))
        fields["upload_id"] = await self._uploads.photo(media, Destination("stories"))
        return await self._configure("media/configure_to_story/", fields)

    async def status(self, upload_id: str) -> Publication:
        """Read a publication's status by the ``upload_id`` its publish call returned."""
        if not isinstance(upload_id, str) or not upload_id.isascii() or not upload_id.isdigit():
            raise ValueError("upload_id must be the numeric ID from a Publication.")
        self._require_session()
        result = await self._transport.request(
            "POST",
            "media/get_upload_status_REST/",
            {"_uuid": self._transport.session.device.id, "post_client_id": upload_id},
        )
        return self._publication(result)

    async def _child(self, entry: CarouselItem, destination: Destination) -> dict[str, Any]:
        media = entry.media
        if isinstance(media, Video):
            upload_id = await self._uploads.video(media, destination)
            fields = _video_fields(media)
        else:
            upload_id = await self._uploads.photo(media, destination)
            fields = _photo_fields(media)
        child = {
            "upload_id": upload_id,
            "source_type": "4",
            "audience": "default",
            "timezone_offset": str(self._transport.session.profile.timezone_offset),
        }
        # The app serializes each carousel child's nested values as JSON strings.
        child.update({k: v if isinstance(v, str) else _json(v) for k, v in fields.items()})
        if entry.user_tags:
            child["usertags"] = _tags(entry.user_tags)
        if entry.alt_text is not None:
            child["custom_accessibility_caption"] = entry.alt_text
        return child

    def _post_fields(self, settings: PostOptions) -> dict[str, Any]:
        fields: dict[str, Any] = {**self._common_fields(), "caption": settings.caption}
        if settings.location is not None:
            fields["location"] = settings.location.location_field()
        # Feed posts send these switches only when they are on, as recorded.
        if settings.comments_disabled:
            fields["disable_comments"] = "1"
        if settings.hide_like_count:
            fields["like_and_view_counts_disabled"] = "1"
        if settings.hide_share_count:
            fields["share_count_disabled"] = "1"
        return fields

    def _common_fields(self) -> dict[str, Any]:
        session = self._transport.session
        fields: dict[str, Any] = {
            "_uid": session.user_id or "",
            "_uuid": session.device.id,
            "device_id": session.device.android_id,
            "timezone_offset": str(session.profile.timezone_offset),
            "source_type": "4",
            "audience": "default",
            "async_publish": "1",
            "publish_id": "1",
        }
        # The device description repeats the user agent's own values, never new telemetry.
        match = _ANDROID.search(session.profile.user_agent)
        if match:
            fields["device"] = {
                "manufacturer": match.group(3),
                "model": match.group(4),
                "android_version": int(match.group(1)),
                "android_release": match.group(2),
            }
        return fields

    async def _configure(self, endpoint: str, fields: dict[str, Any]) -> Publication:
        result = await self._transport.request(
            "POST", endpoint, {"signed_body": "SIGNATURE." + _json(fields)}
        )
        return self._publication(result)

    @staticmethod
    def _publication(result: dict[str, Any]) -> Publication:
        if result.get("status") != "ok":
            raise ProtocolError("Publish response did not confirm success.")
        try:
            publication = Publication.from_dict(result)
            error = result["posts"][0].get("error_info") or {}
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Publish response did not describe the post.") from None
        if error.get("error_type") not in {None, "NO_ERROR"}:
            raise APIError("Instagram reported a publishing error.")
        return publication

    def _require_session(self) -> None:
        if not self._transport.session.authenticated:
            raise LoginRequired("Load your own authenticated session before publishing.")
