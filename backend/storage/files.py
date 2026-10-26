"""Uploaded files, stored encrypted on disk.

Each file is encrypted with AES-256-GCM (encrypted and tamper-checked) using
SENIORLIFE_STORAGE_KEY, under a random name that says nothing about the owner or the
document. The name is bound into the encryption, so a file renamed or swapped with
another one is refused.

The file is only decrypted to a private temporary folder while OCR reads it, and that
folder is removed straight after.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES

MAGIC = b"SLF1"  # format version
NONCE_BYTES = 12
TAG_BYTES = 16
STORAGE_NAME = re.compile(r"[0-9a-f]{32}")


class StoredFileError(RuntimeError):
    """A stored file is missing, damaged, or was encrypted with another key."""


@dataclass(frozen=True)
class StoredFile:
    storage_key: str  # the random file name
    sha256: str  # of the original bytes
    size_bytes: int


class EncryptedFileStore:
    def __init__(self, root: Path | str, key: bytes):
        if len(key) != 32:
            raise ValueError("The storage key must be 32 bytes")
        self.root = Path(root)
        self._key = key

    def _path(self, storage_key: str) -> Path:
        if not STORAGE_NAME.fullmatch(storage_key):  # also stops "../" paths
            raise StoredFileError("Not a storage name")
        return self.root / storage_key[:2] / storage_key

    def save(self, data: bytes) -> StoredFile:
        storage_key = secrets.token_hex(16)
        nonce = secrets.token_bytes(NONCE_BYTES)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        cipher.update(MAGIC + storage_key.encode("ascii"))
        encrypted, tag = cipher.encrypt_and_digest(data)
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(MAGIC + nonce + tag + encrypted)
        os.replace(partial, path)  # never a half-written file under the real name
        return StoredFile(storage_key, hashlib.sha256(data).hexdigest(), len(data))

    def read(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        try:
            blob = path.read_bytes()
        except FileNotFoundError:
            raise StoredFileError("The stored file is missing") from None
        header = len(MAGIC) + NONCE_BYTES + TAG_BYTES
        if len(blob) < header or not blob.startswith(MAGIC):
            raise StoredFileError("The stored file is damaged")
        nonce = blob[len(MAGIC):len(MAGIC) + NONCE_BYTES]
        tag = blob[len(MAGIC) + NONCE_BYTES:header]
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        cipher.update(MAGIC + storage_key.encode("ascii"))
        try:
            return cipher.decrypt_and_verify(blob[header:], tag)
        except ValueError:
            raise StoredFileError("The stored file is damaged or was stored with another key") from None

    @contextmanager
    def decrypted_copy(self, storage_key: str, suffix: str = "") -> Iterator[Path]:
        """A temporary plain copy for OCR, deleted when the block ends (even on errors)."""
        data = self.read(storage_key)
        folder = Path(tempfile.mkdtemp(prefix="seniorlife-"))  # readable by this user only
        try:
            path = folder / f"upload{suffix}"
            path.write_bytes(data)
            yield path
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()
