"""Media files to publish, with dimensions and duration read from the file itself."""

import struct
from dataclasses import dataclass, field
from pathlib import Path

# Bounds guard against reading a malformed container forever, not Instagram's limits.
_MAX_BOXES = 10_000
_START_OF_FRAME = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Return (width, height) from the first JPEG start-of-frame segment."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("Photo must be a JPEG file.")
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise ValueError("JPEG segment structure is invalid.")
        marker = data[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            position += 2
            continue
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        if length < 2:
            raise ValueError("JPEG segment structure is invalid.")
        if marker in _START_OF_FRAME:
            if position + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            if width and height:
                return width, height
            break
        if marker in {0xD9, 0xDA}:
            break
        position += 2 + length
    raise ValueError("JPEG does not declare its dimensions before the image data.")


def _boxes(data: bytes, start: int, end: int) -> dict[bytes, list[tuple[int, int]]]:
    """Index ISO media boxes in one container as {type: [(payload_start, payload_end)]}."""
    found: dict[bytes, list[tuple[int, int]]] = {}
    position = start
    for _ in range(_MAX_BOXES):
        if position + 8 > end:
            return found
        size, kind = struct.unpack(">I4s", data[position : position + 8])
        header = 8
        if size == 1:
            if position + 16 > end:
                break
            (size,) = struct.unpack(">Q", data[position + 8 : position + 16])
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            break
        found.setdefault(kind, []).append((position + header, position + size))
        position += size
    raise ValueError("MP4 box structure is invalid.")


def _movie(data: bytes) -> tuple[dict[bytes, list[tuple[int, int]]], int]:
    """Return the moov box index and the movie duration in milliseconds."""
    top = _boxes(data, 0, len(data))
    if b"ftyp" not in top or b"moov" not in top:
        raise ValueError("Media must be an MP4 file with its metadata (moov) box.")
    moov = _boxes(data, *top[b"moov"][0])
    if b"mvhd" not in moov:
        raise ValueError("MP4 metadata lacks a movie header.")
    start, end = moov[b"mvhd"][0]
    if data[start] == 1 and start + 32 <= end:
        timescale, duration = struct.unpack(">IQ", data[start + 20 : start + 32])
    elif start + 20 <= end:
        timescale, duration = struct.unpack(">II", data[start + 12 : start + 20])
    else:
        raise ValueError("MP4 movie header is truncated.")
    if not timescale or not duration:
        raise ValueError("MP4 does not declare a positive duration.")
    return moov, duration * 1000 // timescale


def _mp4_metadata(data: bytes) -> tuple[int, int, int]:
    """Return (width, height, duration_ms) from moov/mvhd and the first video track."""
    moov, duration_ms = _movie(data)
    for track_start, track_end in moov.get(b"trak", []):
        track = _boxes(data, track_start, track_end)
        if b"mdia" not in track or b"tkhd" not in track:
            continue
        media = _boxes(data, *track[b"mdia"][0])
        handler = media.get(b"hdlr", [(0, 0)])[0]
        if data[handler[0] + 8 : handler[0] + 12] != b"vide":
            continue
        header_start, header_end = track[b"tkhd"][0]
        offset = 88 if data[header_start] == 1 else 76
        if header_start + offset + 8 > header_end:
            raise ValueError("MP4 track header is truncated.")
        width, height = struct.unpack(
            ">II", data[header_start + offset : header_start + offset + 8]
        )
        if not width >> 16 or not height >> 16:
            raise ValueError("MP4 does not declare a positive size.")
        return width >> 16, height >> 16, duration_ms
    raise ValueError("MP4 contains no video track.")


@dataclass(frozen=True, slots=True, repr=False)
class Photo:
    """A JPEG to publish; sent unchanged, with its declared width and height."""

    data: bytes = field(repr=False)
    width: int
    height: int

    @classmethod
    def from_bytes(cls, data: bytes) -> "Photo":
        if not isinstance(data, bytes) or not data:
            raise ValueError("Photo data must be nonempty bytes.")
        width, height = _jpeg_size(data)
        return cls(data, width, height)

    @classmethod
    def from_file(cls, path: str | Path) -> "Photo":
        return cls.from_bytes(Path(path).expanduser().read_bytes())

    def __repr__(self) -> str:
        return f"Photo(width={self.width}, height={self.height}, bytes={len(self.data)})"


@dataclass(frozen=True, slots=True, repr=False)
class Video:
    """An MP4 to publish, sent unchanged; Instagram transcodes it after upload.

    ``cover`` is a JPEG of the first frame. Carousel videos require it because the app
    uploads one; reels and stories let Instagram extract the cover instead.
    """

    data: bytes = field(repr=False)
    width: int
    height: int
    duration_ms: int
    cover: Photo | None = None

    @classmethod
    def from_bytes(cls, data: bytes, cover: Photo | None = None) -> "Video":
        if not isinstance(data, bytes) or not data:
            raise ValueError("Video data must be nonempty bytes.")
        if cover is not None and not isinstance(cover, Photo):
            raise ValueError("Video cover must be a Photo.")
        width, height, duration_ms = _mp4_metadata(data)
        return cls(data, width, height, duration_ms, cover)

    @classmethod
    def from_file(cls, path: str | Path, cover: Photo | None = None) -> "Video":
        return cls.from_bytes(Path(path).expanduser().read_bytes(), cover)

    def __repr__(self) -> str:
        return (
            f"Video(width={self.width}, height={self.height}, "
            f"duration_ms={self.duration_ms}, bytes={len(self.data)}, cover={self.cover!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class Audio:
    """A voice message: an MP4/M4A (AAC) recording, sent unchanged.

    ``waveform`` is what the app draws: one 0-1 level per 100 ms. The default is flat,
    because the library does not decode audio; pass your own levels to show a shape.
    """

    data: bytes = field(repr=False)
    duration_ms: int
    waveform: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        levels = tuple(self.waveform) or (0.0,) * max(1, -(-self.duration_ms // 100))
        if any(type(level) not in {int, float} or not 0 <= level <= 1 for level in levels):
            raise ValueError("Waveform levels must be numbers from 0 to 1.")
        object.__setattr__(self, "waveform", tuple(float(level) for level in levels))

    @classmethod
    def from_bytes(cls, data: bytes) -> "Audio":
        if not isinstance(data, bytes) or not data:
            raise ValueError("Audio data must be nonempty bytes.")
        return cls(data, _movie(data)[1])

    @classmethod
    def from_file(cls, path: str | Path) -> "Audio":
        return cls.from_bytes(Path(path).expanduser().read_bytes())

    def __repr__(self) -> str:
        return f"Audio(duration_ms={self.duration_ms}, bytes={len(self.data)})"
