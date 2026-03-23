"""AES-256-GCM payload encryption.

The Wright Fan API key is used as input key material for HKDF-SHA256 to
derive a 256-bit encryption key.  Each payload is encrypted with a random
12-byte nonce.  The server, which stores the same API key, derives the
identical key and decrypts.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

_SALT = b"wright-telemetry-v1"
_INFO = b"payload-encryption"
_NONCE_LENGTH = 12  # bytes (96 bits, recommended for GCM)
_KEY_LENGTH = 32    # bytes (256 bits)


def derive_key(api_key: str) -> bytes:
    """Derive a 256-bit AES key from the Wright Fan API key using HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=_SALT,
        info=_INFO,
    )
    return hkdf.derive(api_key.encode("utf-8"))


def encrypt_payload(plaintext_dict: dict[str, Any], api_key: str) -> dict[str, str]:
    """Encrypt a JSON-serialisable dict and return the wire format.

    Returns::

        {
            "nonce": "<base64>",
            "ciphertext": "<base64>",  # ciphertext + GCM tag concatenated
        }
    """
    key = derive_key(api_key)
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_LENGTH)
    plaintext = json.dumps(plaintext_dict, separators=(",", ":")).encode("utf-8")
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ct_with_tag).decode(),
    }


def decrypt_payload(wire: dict[str, str], api_key: str) -> dict[str, Any]:
    """Decrypt a wire-format dict back to the original payload.  Useful for testing."""
    key = derive_key(api_key)
    aesgcm = AESGCM(key)
    nonce = base64.b64decode(wire["nonce"])
    ct_with_tag = base64.b64decode(wire["ciphertext"])
    plaintext = aesgcm.decrypt(nonce, ct_with_tag, None)
    return json.loads(plaintext)
