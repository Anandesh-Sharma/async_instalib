"""Recorded rupload exchanges: one raw media body per upload, sent to a fixed upload route.

Post media goes to Instagram's own origin. Direct-message media goes to
``rupload.facebook.com``, as the app sends it, with the same account headers.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

DIRECT_UPLOAD_ORIGIN = "https://rupload.facebook.com"


class UploadRoute(Enum):
    """Upload routes observed in the app; endpoint strings cannot select them."""

    PHOTO = "/rupload_igphoto/"
    VIDEO = "/rupload_igvideo/"
    DIRECT_PHOTO = "/messenger_image/"
    DIRECT_VIDEO = "/messenger_video/"
    DIRECT_AUDIO = "/messenger_audio/"

    @property
    def direct(self) -> bool:
        """Direct-message media, uploaded to ``rupload.facebook.com``."""
        return self.value.startswith("/messenger_")

    @property
    def probes(self) -> bool:
        """The app reads the stored offset before sending these bodies."""
        return self in {UploadRoute.VIDEO, UploadRoute.DIRECT_VIDEO, UploadRoute.DIRECT_AUDIO}

    @property
    def entity_type(self) -> str:
        if self in {UploadRoute.PHOTO, UploadRoute.DIRECT_PHOTO}:
            return "image/jpeg"
        return "audio/mp4" if self is UploadRoute.DIRECT_AUDIO else "video/mp4"


_SEGMENTED = {UploadRoute.VIDEO, UploadRoute.DIRECT_VIDEO}
# Direct uploads name their attachment kind in one recorded header.
_ATTACHMENT_HEADER = {
    UploadRoute.DIRECT_PHOTO: "image_type",
    UploadRoute.DIRECT_VIDEO: "video_type",
    UploadRoute.DIRECT_AUDIO: "audio_type",
}


@dataclass(frozen=True, slots=True, repr=False)
class MediaUpload:
    """One upload: the entity name, recorded rupload parameters, and the media bytes.

    ``params`` is ``None`` for routes the app uploads without a parameter header.
    """

    route: UploadRoute
    name: str
    params: Mapping[str, str] | None = field(repr=False)
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.route, UploadRoute):
            raise ValueError("Upload route must be an UploadRoute.")
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", self.name):
            raise ValueError("Upload entity names contain only letters, digits, _ and -.")
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("Upload content must be nonempty bytes.")
        if self.params is not None and any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in self.params.items()
        ):
            raise ValueError("Upload parameters must be string fields.")

    @property
    def path(self) -> str:
        return self.route.value + self.name

    def offset_headers(self) -> dict[str, str]:
        """Headers of the recorded offset probe, sent before the body."""
        headers = self._route_headers()
        if self.route is UploadRoute.DIRECT_AUDIO:
            return headers
        return {**headers, "Segment-Start-Offset": "0", "Segment-Type": "3"}

    def headers(self) -> dict[str, str]:
        """Headers of the recorded body exchange; the whole file is one segment."""
        headers = {
            **self._route_headers(),
            "X-Entity-Name": self.name,
            "X-Entity-Length": str(len(self.content)),
            "X-Entity-Type": self.route.entity_type,
            "Offset": "0",
            "Content-Type": "application/octet-stream",
        }
        if self.route in _SEGMENTED:
            headers.update({"Segment-Start-Offset": "0", "Segment-Type": "3"})
        return headers

    def _route_headers(self) -> dict[str, str]:
        headers = {}
        if self.params is not None:
            headers["X-Instagram-Rupload-Params"] = json.dumps(
                dict(self.params), separators=(",", ":")
            )
        if self.route in _ATTACHMENT_HEADER:
            headers[_ATTACHMENT_HEADER[self.route]] = "FILE_ATTACHMENT"
        return headers
