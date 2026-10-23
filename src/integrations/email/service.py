"""Email delivery service (Brevo primary, Resend fallback)."""

import logging
import os
import re
from typing import Optional

import httpx

from src.utils.setting import get_settings

logger = logging.getLogger(__name__)


async def _send_brevo(to_email: str, subject: str, html_content: str) -> bool:
    """Send an email via Brevo. Returns True on success, False if not configured/failed."""
    settings = get_settings()
    if not settings.brevo_api_key:
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": settings.brevo_api_key,
        "content-type": "application/json",
    }
    payload = {
        "sender": {
            "name": "Work Pilot",
            "email": settings.brevo_from_email or "noreply@workpilot.app",
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort email delivery
        logger.warning("Brevo email send failed: %s", exc)
        return False


async def _send_resend(to_email: str, subject: str, html_content: str) -> bool:
    """Send an email via Resend. Returns True on success, False if not configured/failed."""
    settings = get_settings()
    if not settings.resend_api_key:
        return False

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": settings.resend_from_email or "Work Pilot <noreply@workpilot.app>",
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort email delivery
        logger.warning("Resend email send failed: %s", exc)
        return False


async def _send_email(to_email: str, subject: str, html_content: str) -> None:
    """Send using Brevo, falling back to Resend. Best-effort: never raises."""
    sent = await _send_brevo(to_email, subject, html_content)
    if not sent:
        await _send_resend(to_email, subject, html_content)


async def send_email_verification_otp(
    email: str, otp: str, expiry_minutes: int = 15
) -> None:
    """Send the email-verification OTP to a new user."""
    subject = "Verify your Work Pilot email"
    html = (
        "<p>Welcome to Work Pilot!</p>"
        f"<p>Your verification code is <strong>{otp}</strong>. "
        f"It expires in {expiry_minutes} minutes.</p>"
    )
    await _send_email(email, subject, html)


async def send_password_reset_otp(email: str, otp: str) -> None:
    """Send the password-reset OTP to a user."""
    subject = "Reset your Work Pilot password"
    html = (
        "<p>We received a request to reset your Work Pilot password.</p>"
        f"<p>Your password reset code is <strong>{otp}</strong>.</p>"
        "<p>If you did not request this, you can ignore this email.</p>"
    )
    await _send_email(email, subject, html)


def _render_organization_invitation_html(
    organization_name: str,
    invite_link: str,
    temporary_password: str = "",
) -> str:
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "utils",
        "templates",
        "organization_invitation.html",
    )
    if not os.path.exists(template_path):
        temp_pwd_html = (
            f"<p>Your temporary password is: <strong>{temporary_password}</strong></p>"
            if temporary_password
            else ""
        )
        return (
            f"<p>You have been invited to join <strong>{organization_name}</strong> on Work Pilot.</p>"
            f"<p><a href='{invite_link}'>Click here to accept the invitation</a></p>"
            f"{temp_pwd_html}"
        )

    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    if temporary_password:
        content = re.sub(
            r"\{\{\s*if\s+\.TempPassword\s*\}\}(.*?)\{\{\s*else\s*\}\}(.*?)\{\{\s*end\s*\}\}",
            r"\1",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(
            r"\{\{\s*if\s+\.TempPassword\s*\}\}(.*?)\{\{\s*end\s*\}\}",
            r"\1",
            content,
            flags=re.DOTALL,
        )
    else:
        content = re.sub(
            r"\{\{\s*if\s+\.TempPassword\s*\}\}(.*?)\{\{\s*else\s*\}\}(.*?)\{\{\s*end\s*\}\}",
            r"\2",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(
            r"\{\{\s*if\s+\.TempPassword\s*\}\}(.*?)\{\{\s*end\s*\}\}",
            "",
            content,
            flags=re.DOTALL,
        )

    content = content.replace("{{ .OrganizationName }}", organization_name)
    content = content.replace("{{.OrganizationName}}", organization_name)
    content = content.replace("{{ .InviteLink }}", invite_link)
    content = content.replace("{{.InviteLink}}", invite_link)
    content = content.replace("{{ .TempPassword }}", temporary_password)
    content = content.replace("{{.TempPassword}}", temporary_password)
    return content


async def send_organization_invitation(
    email: str,
    organization_name: str,
    role_name: str,
    invite_link: str,
    temporary_password: str = "",
) -> None:
    """Send an organization invitation email."""
    subject = f"Invitation to join {organization_name} on Work Pilot"
    html = _render_organization_invitation_html(
        organization_name=organization_name,
        invite_link=invite_link,
        temporary_password=temporary_password,
    )
    await _send_email(email, subject, html)

