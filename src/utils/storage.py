# src/utils/storage.py
"""
S3 Object Storage implementation.
Mirrors internal/pkg/storage/s3.go in Go backend.
"""

from io import BytesIO
from typing import Optional, Tuple
import boto3
from botocore.client import Config

from src.config import get_logger
from src.utils.setting import get_settings

logger = get_logger(__name__)


def get_s3_client():
    """Builds an S3-compatible client (AWS S3 / Supabase S3 / MinIO) using centralized Settings."""
    settings = get_settings()
    endpoint_url = settings.s3_endpoint if settings.s3_endpoint else None

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region or "ap-south-1",
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def upload_comment_attachment_to_s3(
    file_bytes: bytes,
    key: str,
    content_type: str,
) -> str:
    """
    Uploads a comment attachment to S3 and returns the public URL.
    Mirrors UploadCommentAttachment in internal/pkg/storage/s3.go.
    """
    settings = get_settings()
    logger.info("Uploading object to S3 key: %s (size: %d bytes)", key, len(file_bytes))
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
        ContentLength=len(file_bytes),
    )

    public_endpoint = settings.s3_public_endpoint.rstrip("/") if settings.s3_public_endpoint else (
        settings.s3_endpoint.rstrip("/") if settings.s3_endpoint else "https://s3.amazonaws.com"
    )
    public_url = f"{public_endpoint}/{settings.s3_bucket}/{key}"
    logger.info("Attachment successfully uploaded to S3: %s", public_url)
    return public_url


def delete_s3_object(key: str) -> None:
    """
    Deletes an object from S3.
    Mirrors DeleteObject in internal/pkg/storage/s3.go.
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


def get_s3_object(key: str) -> Tuple[any, int, str]:
    """
    Retrieves an object stream from S3.
    Mirrors GetObject in internal/pkg/storage/s3.go.
    Returns (body_stream, content_length, content_type).
    """
    from fastapi import HTTPException, status
    settings = get_settings()
    client = get_s3_client()
    try:
        response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        stream = response["Body"]
        content_length = response.get("ContentLength", 0)
        content_type = response.get("ContentType", "application/octet-stream")
        return stream, content_length, content_type
    except Exception as exc:
        logger.error("Failed to get file from S3 (key: %s): %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve file from storage.",
        ) from exc
