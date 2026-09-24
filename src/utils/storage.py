# src/utils/storage.py
"""
S3 Object Storage implementation.
"""

from io import BytesIO
import re
from typing import Any, Optional, Tuple

import boto3
from botocore.client import Config
from uuid6 import uuid7

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


def sanitize_filename(filename: str) -> str:
    """Return a safe basename suitable for use in an S3 object key."""
    basename = (filename or "file").replace("\\", "/").split("/")[-1]
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", basename)
    sanitized = re.sub(r"_+", "_", sanitized).strip("._-")
    return (sanitized or "file")[:180]


def build_attachment_key(resource_type: str, resource_id: str, filename: str) -> str:
    """Build attachments/<type>/<id>/<unique_filename> without a bucket prefix."""
    normalized_type = resource_type.strip().lower()
    if normalized_type not in {"tasks", "comments", "user_stories"}:
        raise ValueError("resource_type must be 'tasks', 'comments', or 'user_stories'")
    safe_resource_id = re.sub(r"[^A-Za-z0-9_-]", "", str(resource_id))
    if not safe_resource_id:
        raise ValueError("resource_id is required")
    return (
        f"attachments/{normalized_type}/{safe_resource_id}/"
        f"{uuid7()}_{sanitize_filename(filename)}"
    )


def build_logo_key(organization_id: str, filename: str) -> str:
    """Build organizations/logos/<organization_id>/<unique_filename>."""
    safe_org_id = re.sub(r"[^A-Za-z0-9_-]", "", str(organization_id))
    if not safe_org_id:
        raise ValueError("organization_id is required")
    return f"organizations/logos/{safe_org_id}/{uuid7()}_{sanitize_filename(filename)}"


class S3StorageService:
    """Centralized Supabase S3 object operations."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = get_s3_client()

    def upload_bytes(self, file_bytes: bytes, object_key: str, content_type: str) -> str:
        mime_type = content_type or "application/octet-stream"
        logger.info("Uploading S3 object key=%s size=%d", object_key, len(file_bytes))
        try:
            self.client.upload_fileobj(
                BytesIO(file_bytes),
                self.settings.s3_bucket,
                object_key,
                ExtraArgs={"ContentType": mime_type},
            )
        except Exception:
            logger.exception("Failed to upload S3 object key=%s", object_key)
            raise
        return get_public_url(object_key)

    def delete(self, object_key: str) -> None:
        logger.info("Deleting S3 object key=%s", object_key)
        try:
            self.client.delete_object(
                Bucket=self.settings.s3_bucket,
                Key=object_key,
            )
        except Exception:
            logger.exception("Failed to delete S3 object key=%s", object_key)
            raise

    def get(self, object_key: str) -> Tuple[Any, int, str]:
        response = self.client.get_object(
            Bucket=self.settings.s3_bucket,
            Key=object_key,
        )
        return (
            response["Body"],
            response.get("ContentLength", 0),
            response.get("ContentType", "application/octet-stream"),
        )

    def list_objects(self, prefix: str = "attachments/") -> list[dict[str, Any]]:
        """List and log objects below a prefix for storage diagnostics."""
        response = self.client.list_objects_v2(
            Bucket=self.settings.s3_bucket,
            Prefix=prefix,
        )
        objects = response.get("Contents", [])
        for item in objects:
            logger.info("S3 object key=%s size=%s", item.get("Key"), item.get("Size"))
        return objects


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
    public_url = S3StorageService().upload_bytes(file_bytes, key, content_type)
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
    S3StorageService().delete(key)
    logger.info("Deleted object from S3: %s", key)


def upload_logo(
    file: BytesIO,
    filename: str,
    content_type: str = "image/*",
    organization_id: str | None = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Upload an organization logo and return (url, storage_key)."""
    if not organization_id:
        raise ValueError("organization_id is required for logo uploads")
    file_bytes = file.getvalue() if hasattr(file, "getvalue") else file.read()
    settings = get_settings()
    max_size = int(settings.s3_max_file_size_mb or 5) * 1024 * 1024
    if not file_bytes:
        raise ValueError("Logo file is empty")
    if len(file_bytes) > max_size:
        raise ValueError(
            f"Logo exceeds the maximum allowed size of {settings.s3_max_file_size_mb} MB"
        )

    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        detected_type = "image/png"
    elif file_bytes.startswith(b"\xff\xd8\xff"):
        detected_type = "image/jpeg"
    elif (
        len(file_bytes) >= 12
        and file_bytes.startswith(b"RIFF")
        and file_bytes[8:12] == b"WEBP"
    ):
        detected_type = "image/webp"
    else:
        raise ValueError("Invalid logo type; only PNG, JPG/JPEG, and WEBP are supported")

    key = build_logo_key(organization_id, filename)
    url = upload_s3_object(file_bytes, key, detected_type)
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

    try:
        return S3StorageService().get(key)
    except StorageConfigurationError:
        raise
    except Exception as exc:
        logger.error("Failed to get file from S3 (key: %s): %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve file from storage.",
        ) from exc
