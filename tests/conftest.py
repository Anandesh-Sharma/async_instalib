import base64
import struct

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def key_headers(private_key):
    public = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return {
        "ig-set-password-encryption-key-id": "9",
        "ig-set-password-encryption-pub-key": base64.b64encode(public).decode(),
    }


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def make_jpeg(width: int = 1080, height: int = 1350) -> bytes:
    """A minimal JPEG header: start of image, one APP0 segment, and a baseline frame."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + frame + b"\xff\xd9"


def make_mp4(
    width: int = 1080, height: int = 1920, duration_ms: int = 5782, version: int = 0
) -> bytes:
    """A minimal MP4 with version 0 or 1 headers and one video track."""
    if version:
        timing = b"\x01" + b"\x00" * 19 + struct.pack(">IQ", 1000, duration_ms)
        track = b"\x01" + b"\x00" * 87
    else:
        timing = b"\x00" * 12 + struct.pack(">II", 1000, duration_ms)
        track = b"\x00" * 76
    mvhd = _box(b"mvhd", timing + b"\x00" * 80)
    tkhd = _box(b"tkhd", track + struct.pack(">II", width << 16, height << 16))
    hdlr = _box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 13)
    trak = _box(b"trak", tkhd + _box(b"mdia", hdlr))
    return _box(b"ftyp", b"isom\x00\x00\x02\x00") + _box(b"moov", mvhd + trak) + b"\x00" * 64


@pytest.fixture
def jpeg():
    return make_jpeg()


@pytest.fixture
def mp4():
    return make_mp4()
