"""Encrypted cold storage for original uploads after short-term processing.

Original files stay hot in ``UPLOAD_DIR`` for the configured retention window,
then are encrypted with a dedicated Fernet key and moved into ``COLD_STORAGE_DIR``.
The key is kept outside the archive directory and uses owner-only permissions.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet

from app.core.config import settings

_KEY_FILENAME = "cold_storage.key"


def cold_storage_dir() -> str:
    path = getattr(settings, "COLD_STORAGE_DIR", "") or os.path.join(settings.DATA_DIR, "cold_storage")
    os.makedirs(path, exist_ok=True)
    return path


def cold_storage_key_path() -> str:
    return os.path.join(settings.DATA_DIR, _KEY_FILENAME)


def _fernet() -> Fernet:
    key_path = cold_storage_key_path()
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    if not os.path.exists(key_path):
        with open(key_path, "wb") as fh:
            fh.write(Fernet.generate_key())
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    with open(key_path, "rb") as fh:
        return Fernet(fh.read().strip())


def encrypted_path(file_id: str) -> str:
    return os.path.join(cold_storage_dir(), f"{file_id}.enc")


def metadata_path(file_id: str) -> str:
    return os.path.join(cold_storage_dir(), f"{file_id}.meta.enc")


def archive_metadata(file_id: str, metadata: dict) -> str:
    import json

    raw = json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")
    token = _fernet().encrypt(raw)
    destination = metadata_path(file_id)
    temp_path = destination + ".tmp"
    with open(temp_path, "wb") as fh:
        fh.write(token)
    os.replace(temp_path, destination)
    return destination


def restore_metadata(file_id: str) -> dict:
    import json

    source = metadata_path(file_id)
    if not os.path.isfile(source):
        return {}
    with open(source, "rb") as fh:
        raw = _fernet().decrypt(fh.read())
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def archive_original_to_cold(file_id: str, file_path: str) -> str:
    """Encrypt ``file_path`` into cold storage and remove the hot original."""
    if not isinstance(file_path, str) or not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    with open(file_path, "rb") as fh:
        token = _fernet().encrypt(fh.read())

    destination = encrypted_path(file_id)
    temp_path = destination + ".tmp"
    with open(temp_path, "wb") as fh:
        fh.write(token)
    os.replace(temp_path, destination)

    os.remove(file_path)
    return destination


def restore_original_from_cold(file_id: str, target_path: str) -> str:
    """Decrypt a cold-stored original back to ``target_path``."""
    source = encrypted_path(file_id)
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    with open(source, "rb") as fh:
        data = _fernet().decrypt(fh.read())

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    temp_path = target_path + ".tmp"
    with open(temp_path, "wb") as fh:
        fh.write(data)
    os.replace(temp_path, target_path)
    return target_path


__all__ = [
    "archive_metadata",
    "archive_original_to_cold",
    "cold_storage_dir",
    "encrypted_path",
    "metadata_path",
    "restore_metadata",
    "restore_original_from_cold",
]
