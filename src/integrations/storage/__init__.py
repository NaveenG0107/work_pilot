"""Storage integration package."""

from src.integrations.storage.service import (
    upload_avatar,
    delete_object,
    key_from_url,
)

__all__ = [
    "upload_avatar",
    "delete_object",
    "key_from_url",
]
