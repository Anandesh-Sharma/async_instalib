"""Encode the mobile password envelope using the server's current public key."""

import base64
import os
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..exceptions import ProtocolError


@dataclass(frozen=True, slots=True)
class PasswordKey:
    """A server-issued public encryption key; never a client secret or hardcoded key."""

    id: int
    encoded_key: str

    def encrypt(self, password: str) -> str:
        if not 0 <= self.id <= 255:
            raise ProtocolError("Password-key identifier is outside the supported byte range.")
        try:
            raw_key = base64.b64decode(self.encoded_key, validate=True)
            public_key = (
                serialization.load_pem_public_key(raw_key)
                if raw_key.startswith(b"-----BEGIN")
                else serialization.load_der_public_key(raw_key)
            )
        except (ValueError, TypeError):
            raise ProtocolError("Instagram returned an invalid password public key.") from None
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise ProtocolError("Instagram returned an unsupported password-key algorithm.")
        timestamp = str(int(time.time()))
        key, nonce = os.urandom(32), os.urandom(12)
        wrapped_key = public_key.encrypt(key, padding.PKCS1v15())
        encrypted = AESGCM(key).encrypt(nonce, password.encode("utf-8"), timestamp.encode("ascii"))
        ciphertext, tag = encrypted[:-16], encrypted[-16:]
        envelope = (
            bytes((1, self.id))
            + nonce
            + len(wrapped_key).to_bytes(2, "little")
            + wrapped_key
            + tag
            + ciphertext
        )
        return f"#PWD_INSTAGRAM:4:{timestamp}:{base64.b64encode(envelope).decode('ascii')}"
