"""Email integration package."""

from src.integrations.email.service import (
    send_email_verification_otp,
    send_organization_invitation,
    send_password_reset_otp,
)

__all__ = [
    "send_email_verification_otp",
    "send_organization_invitation",
    "send_password_reset_otp",
]

