"""Media uploads with the rupload parameters recorded for each destination."""

import json
import secrets
import time
from dataclasses import dataclass
from typing import Literal

from ..exceptions import ProtocolError
from ..models.uploads import Photo, Video
from ..transport import Transport
from ..transport.upload import MediaUpload, UploadRoute

# Recorded first-attempt values; the client never retries an upload.
_RETRY_CONTEXT = json.dumps(
    {
        "num_reupload": 0,
        "cover_photo_re_upload_count": 0,
        "num_step_manual_retry": 0,
        "num_step_auto_retry": 0,
    },
    separators=(",", ":"),
)


@dataclass(frozen=True, slots=True)
class Destination:
    """Where an upload will be published; carousel items share the album's session.

    ``comment`` is a photo comment: the app sends no share type and no dimensions.
    """

    share_type: Literal["feed", "reels", "stories", "comment"]
    session_id: str | None = None

    @property
    def sidecar(self) -> bool:
        return self.session_id is not None


class MediaUploads:
    """Send each media body once and return the upload ID that configure requests name."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._last_id = 0

    def new_id(self) -> str:
        """A millisecond upload ID, unique within this client even for quick carousels."""
        self._last_id = max(self._last_id + 1, time.time_ns() // 1_000_000)
        return str(self._last_id)

    async def photo(self, photo: Photo, destination: Destination) -> str:
        """Upload a JPEG unchanged, as the app uploads JPEG video covers."""
        upload_id = self.new_id()
        await self._send_photo(photo, self._photo_params(photo, upload_id, destination))
        return upload_id

    async def video(self, video: Video, destination: Destination) -> str:
        """Upload an MP4 unchanged; carousel videos also upload their JPEG cover."""
        upload_id = self.new_id()
        params = {
            "upload_id": upload_id,
            "session_id": destination.session_id or upload_id,
            "media_type": "2",
            "upload_media_width": str(video.width),
            "upload_media_height": str(video.height),
            "upload_media_duration_ms": str(video.duration_ms),
            "share_type": destination.share_type,
            "content_tags": "use_default_cover",
            "debug_segment_id": "0",
            "upload_engine_config_enum": "0",
            "is_optimistic_upload": "false",
            "xsharing_user_ids": "[]",
            "retry_context": _RETRY_CONTEXT,
            "stella_data": "{}",
        }
        if destination.sidecar:
            params["is_sidecar"] = "1"
        else:
            # Reels and stories let Instagram take the cover from the video.
            params["extract_cover_frame"] = "1"
        if destination.share_type == "reels":
            params["is_clips_video"] = "1"
        if destination.share_type == "stories":
            params["for_album"] = "1"
        second = int(time.time()) * 1000
        name = f"{secrets.token_hex(16)}-0-{len(video.data)}-{second}-{second}"
        result = await self._transport.upload(
            MediaUpload(UploadRoute.VIDEO, name, params, video.data)
        )
        if result.get("status") != "ok":
            raise ProtocolError("Video upload did not confirm success.")
        if video.cover is not None and destination.sidecar:
            params = self._photo_params(video.cover, upload_id, destination)
            params.update(media_type="2", cover_photo_type="first_frame")
            await self._send_photo(video.cover, params)
        return upload_id

    @staticmethod
    def _photo_params(photo: Photo, upload_id: str, destination: Destination) -> dict[str, str]:
        compression: dict[str, object] = {"lib_name": "moz", "lib_version": "5.0.0", "quality": "0"}
        params = {
            "upload_id": upload_id,
            "session_id": destination.session_id or upload_id,
            "media_type": "1",
            "upload_engine_config_enum": "0",
            "is_optimistic_upload": "false",
            "xsharing_user_ids": "[]",
            "retry_context": _RETRY_CONTEXT,
            "stella_data": "{}",
        }
        if destination.share_type == "comment":
            params["upload_engine_config_enum"] = "25"
        else:
            params["share_type"] = destination.share_type
            compression.update(original_width=photo.width, original_height=photo.height)
        params["image_compression"] = json.dumps(compression, separators=(",", ":"))
        if destination.sidecar:
            params["is_sidecar"] = "1"
        return params

    async def _send_photo(self, photo: Photo, params: dict[str, str]) -> None:
        upload_id = params["upload_id"]
        name = f"{upload_id}_0_{secrets.randbelow(2**31)}"
        result = await self._transport.upload(
            MediaUpload(UploadRoute.PHOTO, name, params, photo.data)
        )
        if result.get("status") != "ok" or str(result.get("upload_id")) != upload_id:
            raise ProtocolError("Photo upload did not confirm the requested upload ID.")
