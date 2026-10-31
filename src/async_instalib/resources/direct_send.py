"""Send every recorded direct-message kind; each send is one request after any upload.

Sends are never retried: Instagram may have delivered a message even when the network
fails, and a repeat would arrive twice.
"""

import json
import secrets
import time
from typing import Any

from ..exceptions import ProtocolError
from ..models.content import Media
from ..models.direct import Message
from ..models.uploads import Audio, Photo, Video
from ..transport import Transport
from ..transport.upload import MediaUpload, UploadRoute
from .direct_common import Operation, acknowledged, form, mutation, new_otid, numeric, thread_id

_CLIP_SHARE = Operation(
    "IGDirectClipShareMutationWithResponse",
    "114459377817297228431979708407",
    "xig_direct_clip_share_with_response",
)
_PROFILE_SHARE = Operation(
    "IGDirectBroadcastProfile",
    "11560437748732398101273075496",
    "ig_direct_broadcast_profile",
)
# Clip-share inputs the app sends as null when not sharing from search or ads.
_CLIP_NULLS = (
    "updated_blend_id",
    "translation_delivery_method",
    "media_share_version",
    "chaining_session_id",
    "serp_session_id",
    "reshared_ad_id",
    "ai_assistant_extras",
    "ranking_info_token",
    "pigeon_session_id",
    "search_session_id",
    "inventory_source",
    "social_context_share_type",
    "blend_id",
    "nav_chain",
    "client_position",
    "tracking_token",
    "rank_token",
    "blend_recommender_igids",
    "query_text",
)


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Message text must be nonempty.")
    return value


class DirectSender:
    """``instagram.direct.send``: one method per message kind; each returns the sent Message."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def text(self, thread: str, text: str, reply_to: Message | None = None) -> Message:
        """Send text; pass a text Message as ``reply_to`` to quote it."""
        fields = {
            "text": _text(text),
            "is_x_transport_forward": "false",
            "send_silently": "false",
            "is_written_with_ai": "0",
        }
        if reply_to is not None:
            if not isinstance(reply_to, Message) or reply_to.client_context is None:
                raise ValueError("reply_to must be a Message from the inbox or a send.")
            if reply_to.type != "text":
                raise ValueError("Replies were recorded for text messages only.")
            fields.update(
                replied_to_item_id=reply_to.id,
                replied_to_client_context=reply_to.client_context,
                replied_to_user_id=reply_to.user_id or self._transport.session.user_id or "",
                replied_to_target_type="text",
                replied_to_action_source="long_press",
            )
        return await self._broadcast("text", thread, fields)

    async def link(self, thread: str, text: str, urls: list[str]) -> Message:
        """Send text containing links; ``urls`` are the links in ``text``, shown as a preview."""
        if not urls or any(not isinstance(url, str) or url not in text for url in urls):
            raise ValueError("urls must be the links that appear in text.")
        fields = {
            "link_text": _text(text),
            "link_urls": json.dumps(urls),
            "is_x_transport_forward": "false",
        }
        return await self._broadcast("link", thread, fields)

    async def photo(self, thread: str, photo: Photo) -> Message:
        """Upload a JPEG to Meta's upload host, then send it."""
        if not isinstance(photo, Photo):
            raise ValueError("photo must be a Photo.")
        upload_id = str(time.time_ns() // 1_000_000)
        attachment = await self._upload(
            MediaUpload(
                UploadRoute.DIRECT_PHOTO,
                f"{upload_id}_0_{secrets.randbelow(2**31)}",
                {"is_optimistic_upload": "true"},
                photo.data,
            )
        )
        fields = {
            "attachment_fbid": attachment,
            "is_duplicate_attachment": "false",
            "meta_gallery_media_info": "[]",
            "allow_full_aspect_ratio": "true",
            "is_x_transport_forward": "false",
        }
        return await self._broadcast("photo_attachment", thread, fields)

    async def video(self, thread: str, video: Video) -> Message:
        """Upload an MP4 to Meta's upload host, then send it."""
        if not isinstance(video, Video):
            raise ValueError("video must be a Video.")
        second = int(time.time()) * 1000
        name = f"{secrets.token_hex(16)}-0-{len(video.data)}-{second}-{second}"
        attachment = await self._upload(
            MediaUpload(UploadRoute.DIRECT_VIDEO, name, None, video.data)
        )
        fields = {
            "attachment_fbid": attachment,
            "video_result": attachment,
            "is_duplicate_attachment": "false",
            "meta_gallery_media_info": "[]",
            "is_x_transport_forward": "false",
        }
        return await self._broadcast("video_attachment", thread, fields)

    async def voice(self, thread: str, audio: Audio) -> Message:
        """Upload an MP4/M4A recording, then send it as a voice message."""
        if not isinstance(audio, Audio):
            raise ValueError("audio must be an Audio.")
        upload_id = str(time.time_ns() // 1_000_000)
        attachment = await self._upload(
            MediaUpload(
                UploadRoute.DIRECT_AUDIO,
                f"{upload_id}_0_{secrets.randbelow(2**31)}",
                None,
                audio.data,
            )
        )
        fields = {
            "attachment_fbid": attachment,
            "upload_id": upload_id,
            "waveform": json.dumps(list(audio.waveform)),
            "waveform_sampling_frequency_hz": "10",
        }
        return await self._broadcast("voice_attachment", thread, fields)

    async def like(self, thread: str) -> Message:
        """Send the heart sticker."""
        return await self._broadcast("like", thread, {})

    async def gif(self, thread: str, giphy_id: str) -> Message:
        """Send a GIF by its GIPHY ID (the ID in a giphy.com URL)."""
        if not isinstance(giphy_id, str) or not giphy_id.isalnum():
            raise ValueError("giphy_id must be the alphanumeric GIPHY ID.")
        fields = {"id": giphy_id, "gif_category_position": "popular"}
        return await self._broadcast("animated_media", thread, fields)

    async def media(self, thread: str, media: Media, text: str | None = None) -> str:
        """Share a photo post or a reel; returns the new message's item ID.

        Reels can carry ``text``. Sharing carousels and feed videos was not recorded.
        """
        if not isinstance(media, Media):
            raise ValueError("media must be a Media from a profile, feed, or publication.")
        pk = media.id.split("_")[0]
        if media.product_type == "clips":
            data: dict[str, Any] = {
                **dict.fromkeys(_CLIP_NULLS),
                "translated_language": "en",
                "text": {"sensitive_string_value": _text(text)} if text is not None else None,
                "should_skip_genai_eval": False,
                "send_attribution": "feed_contextual_profile",
                "send_silently": False,
                "forwarding_metadata": {
                    "sampled": False,
                    "is_x_transport_forward": False,
                    "e2e_tracking_ts": int(time.time()),
                },
            }
            variables = {
                "otid": new_otid(),
                "ig_thread_igid": thread_id(thread),
                "ig_media_igid": pk,
                "is_shh_mode": False,
                "data": data,
            }
            result = await mutation(self._transport, _CLIP_SHARE, variables)
            item = result.get("ig_item_id") if isinstance(result, dict) else None
            if not isinstance(item, str):
                raise ProtocolError("Instagram did not acknowledge the shared reel.")
            return item
        if media.media_type != 1 or text is not None:
            raise ValueError("Only photo posts (without text) and reels were recorded.")
        sent = await self._broadcast("media_share?media_type=photo", thread, {"media_id": media.id})
        return sent.id

    async def profile(self, thread: str, user_id: str) -> None:
        """Share a person's profile."""
        variables = {
            "send_silently": False,
            "otid": new_otid(),
            "is_shh_mode": False,
            "is_x_transport_forward": False,
            "send_attribution": "profile",
            "ig_profile_user_igid": numeric(user_id, "user_id"),
            "ig_thread_igid": thread_id(thread),
        }
        if await mutation(self._transport, _PROFILE_SHARE, variables) is not True:
            raise ProtocolError("Instagram did not confirm the profile share.")

    async def forward(self, thread: str, message: Message) -> Message:
        """Forward a message (from any of your threads) to ``thread``."""
        if not isinstance(message, Message):
            raise ValueError("message must be a Message.")
        fields = {
            "forwarded_from_thread_id": message.thread_id,
            "forwarded_from_thread_item_id": message.id,
            "forward_score": "0",
        }
        return await self._broadcast("forward", thread, fields)

    async def _upload(self, upload: MediaUpload) -> str:
        result = await self._transport.upload(upload)
        media_id = result.get("media_id")
        if type(media_id) is not int and not (isinstance(media_id, str) and media_id.isdigit()):
            raise ProtocolError("Media upload did not return its attachment ID.")
        return str(media_id)

    async def _broadcast(self, kind: str, thread: str, fields: dict[str, str]) -> Message:
        otid = new_otid()
        common = {
            "action": "send_item",
            "thread_ids": f"[{thread_id(thread)}]",
            "send_attribution": "inbox",
            "client_context": otid,
            "mutation_token": otid,
            "offline_threading_id": otid,
            "device_id": self._transport.session.device.android_id,
        }
        path, _, query = kind.partition("?")
        endpoint = f"direct_v2/threads/broadcast/{path}/" + (f"?{query}" if query else "")
        result = await form(self._transport, endpoint, {**common, **fields})
        return acknowledged(result, path)
