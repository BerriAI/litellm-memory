"""Ente-compatible libsodium encryption for work records and delegated keys.

Blob format follows ente/ente web/packages/base/crypto/libsodium.ts at
d3839ab34cfec2762f720e9d5fd7c14e1151d064: secretstream XChaCha20-Poly1305,
one final chunk, standard base64. Key wrapping uses secretbox as Ente does.
The application is an authorized hosted processor, not a zero-knowledge client.
"""

import base64
import json
import os
from pathlib import Path

from nacl import bindings, secret, utils


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def unb64(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def new_key() -> bytes:
    return utils.random(secret.SecretBox.KEY_SIZE)


def encrypt_blob(value, key: bytes) -> dict:
    state = bindings.crypto_secretstream_xchacha20poly1305_state()
    header = bindings.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    encrypted = bindings.crypto_secretstream_xchacha20poly1305_push(
        state, encode(value), tag=bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL,
    )
    return {"encryptedData": b64(encrypted), "header": b64(header)}


def decrypt_blob(blob: dict, key: bytes):
    state = bindings.crypto_secretstream_xchacha20poly1305_state()
    bindings.crypto_secretstream_xchacha20poly1305_init_pull(state, unb64(blob["header"]), key)
    data, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(state, unb64(blob["encryptedData"]))
    if tag != bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL:
        raise ValueError("Incomplete encrypted record")
    return json.loads(data)


class KeyVault:
    def __init__(self, state_dir: Path, configured_key: str = ""):
        if configured_key:
            key = unb64(configured_key)
        else:
            path = state_dir / "vault-wrapping-key"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(new_key())
                    handle.flush()
                    os.fsync(handle.fileno())
            key = path.read_bytes()
        self.box = secret.SecretBox(key)

    def wrap(self, owner: str, key: bytes) -> str:
        return b64(bytes(self.box.encrypt(encode({"owner": owner, "key": b64(key)}))))

    def unwrap(self, owner: str, wrapped: str) -> bytes:
        data = json.loads(self.box.decrypt(unb64(wrapped)))
        if data["owner"] != owner:
            raise ValueError("Key belongs to a different account")
        return unb64(data["key"])

    def namespace_key(self):
        """Ente's entity-key envelope; record keys remain separate per owner."""
        encrypted = self.box.encrypt(new_key())
        return {"encryptedKey": b64(encrypted.ciphertext), "header": b64(encrypted.nonce)}

    def seal_operational(self, value) -> str:
        return "enc:v1:" + b64(bytes(self.box.encrypt(encode(value))))

    def open_operational(self, value: str):
        if not value.startswith("enc:v1:"):
            raise ValueError("Operational data is not encrypted")
        return json.loads(self.box.decrypt(unb64(value.removeprefix("enc:v1:"))))
