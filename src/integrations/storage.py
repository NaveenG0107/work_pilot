from __future__ import annotations

import logging
import os
from typing import Optional

from src.utils.setting import get_settings


logger = logging.getLogger(__name__)


def _client():
    """Return a boto3 S3 client configured from settings, or None if unconfigured."""
    settings = get_settings()
    if not (settings.s3_access_key_id and settings.s3_secret_access_key and settings.s3_bucket):
        return None

    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint or None,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region or None,
    )


def _public_url(key: str) -> Optional[str]:
    settings = get_settings()
    base = settings.s3_public_endpoint or settings.s3_endpoint
    if not base:
        return None
    base = base.rstrip("/")
    return f"{base}/{settings.s3_bucket}/{key}"


def upload_avatar(file_bytes: bytes, filename: str, content_type: str | None = None) -> Optional[str]:
    """
    Upload an avatar image to S3/Supabase storage.

    Returns the public URL, or None when storage is not configured (so callers
    can continue without an avatar).
    """
    client = _client()
    if client is None:
        logger.info("S3 storage not configured; skipping avatar upload")
        return None

    settings = get_settings()
    ext = os.path.splitext(filename)[1] or ""
    import uuid as _uuid

    key = f"avatars/{_uuid.uuid4().hex}{ext}"

    extra_args = {"ContentType": content_type} if content_type else {}
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=file_bytes,
        **extra_args,
    )
    return _public_url(key)


def delete_object(key: str) -> None:
    """Delete an object from S3/Supabase storage (best-effort)."""
    client = _client()
    if client is None:
        return
    settings = get_settings()
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete object %s: %s", key, exc)


def key_from_url(url: str) -> Optional[str]:
    """Extract the object key from a public storage URL (for deletion)."""
    settings = get_settings()
    base = settings.s3_public_endpoint or settings.s3_endpoint
    if not base or not url.startswith(base):
        return None
    suffix = url[len(base.rstrip("/")) + 1 :]
    if suffix.startswith(f"{settings.s3_bucket}/"):
        return suffix[len(settings.s3_bucket) + 1 :]
    return suffix
