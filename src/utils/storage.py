# src/utils/storage.py
"""
Object-storage (logo upload) stub (shared infrastructure).

TODO: Wire a real object-storage client (e.g. AWS S3 / MinIO) and replace
these stubs. Mirrors internal/pkg/storage UploadLogo / DeleteObject in Go.
"""

from io import BytesIO
from typing import Optional, Tuple

from src.config import get_logger

logger = get_logger(__name__)


def upload_logo(file: BytesIO, filename: str, content_type: str = "image/*") -> Tuple[Optional[str], Optional[str]]:
    """Upload an organization logo and return (url, storage_key).

    Currently a stub that logs the intended upload and returns (None, None).
    """
    logger.warning(
        "FILE NOT UPLOADED (stub) — organization logo",
        extra={"file_name": filename, "content_type": content_type},
    )
    return None, None


def delete_object(storage_key: str) -> None:
    """Delete a previously uploaded object.

    Currently a stub that logs the intended deletion.
    """
    logger.warning("FILE NOT DELETED (stub) — storage key: %s", storage_key)
