# src/utils/storage.py
"""
S3 Object Storage implementation.
"""

from io import BytesIO
from typing import Any, Optional, Tuple

import boto3
from botocore.client import Config

from src.config import get_logger
from src.utils.setting import get_settings

logger = get_logger(__name__)


class StorageConfigurationError(RuntimeError):
    """Raised when required S3/Supabase configuration is missing."""


def validate_s3_configuration() -> None:
    """Ensure all settings required for Supabase S3 operations are present."""
    settings = get_settings()
    required = {
        "S3_ENDPOINT": settings.s3_endpoint,
        "S3_ACCESS_KEY_ID": settings.s3_access_key_id,
        "S3_SECRET_ACCESS_KEY": settings.s3_secret_access_key,
        "S3_BUCKET": settings.s3_bucket,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise StorageConfigurationError(
            f"S3 storage is not configured; missing: {', '.join(missing)}"
        )


def get_s3_client():
    """Builds an S3-compatible client (AWS S3 / Supabase S3 / MinIO) using centralized Settings."""
    validate_s3_configuration()
    settings = get_settings()

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region or "ap-south-1",
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def get_public_url(key: str) -> str:
    """Build the public object URL for an S3 storage key."""
    validate_s3_configuration()
    settings = get_settings()
    endpoint = (settings.s3_public_endpoint or settings.s3_endpoint).rstrip("/")
    if endpoint.endswith("/s3"):
        endpoint = endpoint[:-3] + "/object/public"
    elif "/s3/" in endpoint:
        endpoint = endpoint.replace("/s3/", "/object/public/", 1)
    elif "/object/public" not in endpoint:
        endpoint += "/object/public"
    return f"{endpoint}/{settings.s3_bucket}/{key}"


def upload_s3_object(
    file_bytes: bytes,
    key: str,
    content_type: str,
) -> str:
    """Upload bytes to S3-compatible storage and return their public URL."""
    settings = get_settings()
    logger.info(
        "Uploading object to S3 key: %s (size: %d bytes)", key, len(file_bytes)
    )
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
        ContentLength=len(file_bytes),
    )
    public_url = get_public_url(key)
    logger.info("Attachment successfully uploaded to S3: %s", public_url)
    return public_url


def upload_comment_attachment_to_s3(
    file_bytes: bytes,
    key: str,
    content_type: str,
) -> str:
    """Backward-compatible wrapper used by the comment attachment service."""
    return upload_s3_object(file_bytes, key, content_type)


def delete_s3_object(key: str) -> None:
    """
    Deletes an object from S3.
    """
    settings = get_settings()
    logger.info("Deleting object from S3: %s", key)
    client = get_s3_client()
    client.delete_object(Bucket=settings.s3_bucket, Key=key)
    logger.info("Deleted object from S3: %s", key)


def upload_logo(file: BytesIO, filename: str, content_type: str = "image/*") -> Tuple[Optional[str], Optional[str]]:
    """Upload an organization logo and return (url, storage_key)."""
    key = f"organizations/logos/{filename}"
    file_bytes = file.getvalue() if hasattr(file, "getvalue") else file.read()
    url = upload_comment_attachment_to_s3(file_bytes, key, content_type)
    return url, key


def delete_object(storage_key: str) -> None:
    """Delete a previously uploaded object."""
    delete_s3_object(storage_key)


def get_s3_object(key: str) -> Tuple[Any, int, str]:
    """
    Retrieves an object stream from S3.
    Returns (body_stream, content_length, content_type).
    """
    from fastapi import HTTPException, status

    settings = get_settings()
    try:
        client = get_s3_client()
        response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        stream = response["Body"]
        content_length = response.get("ContentLength", 0)
        content_type = response.get("ContentType", "application/octet-stream")
        return stream, content_length, content_type
    except StorageConfigurationError:
        raise
    except Exception as exc:
        logger.error("Failed to get file from S3 (key: %s): %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve file from storage.",
        ) from exc
