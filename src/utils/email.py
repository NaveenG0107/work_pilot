# src/utils/email.py
"""
Email sending via Brevo (primary) with Resend as fallback.
HTML templates live in src/utils/templates/. Credentials are read from env
(BREVO_*/RESEND_*) and can be left empty until provisioned.
"""

import json
import logging
import os
import re
from typing import Any, Dict, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from src.config import (
    BREVO_API_KEY,
    BREVO_FROM_EMAIL,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
    get_logger,
)

logger = get_logger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Where HTML email templates live.
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


# ------------------------------------------------------------ template engine


def _render_template(template_name: str, data: Dict[str, Any]) -> str:
    """Render a Go-template-style HTML file (supports `{{ .X }}` and
    `{{ if .X }}...{{ else }}...{{ end }}`)."""
    path = os.path.join(_TEMPLATES_DIR, template_name)
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    return _render_go_template(source, data)


def _find_block_bounds(source: str, if_brace: int):
    """Given the index of `{{` of an `if` tag, locate the matching
    `{{ else }}` (if any) and `{{ end }}`. Returns (else_brace, end_brace)."""
    depth = 1
    k = source.find("}}", if_brace) + 2
    else_brace = None
    while k < len(source):
        m = source.find("{{", k)
        if m == -1:
            break
        mj = source.find("}}", m)
        if mj == -1:
            break
        tag = source[m + 2 : mj].strip()
        if tag.startswith("if "):
            depth += 1
            k = mj + 2
        elif tag == "else":
            if depth == 1:
                else_brace = m
            k = mj + 2
        elif tag == "end":
            depth -= 1
            if depth == 0:
                return else_brace, m
            k = mj + 2
        else:
            k = mj + 2
    return else_brace, None


def _render_go_template(source: str, data: Dict[str, Any]) -> str:
    out: list = []

    def tag_end(brace: int) -> int:
        return source.find("}}", brace) + 2

    i = 0
    n = len(source)
    while i < n:
        brace = source.find("{{", i)
        if brace == -1:
            out.append(source[i:])
            break
        out.append(source[i:brace])
        close = source.find("}}", brace)
        if close == -1:
            out.append(source[brace:])
            break
        raw = source[brace + 2 : close].strip()

        if raw.startswith("if "):
            field = raw[3:].strip().lstrip(".")
            truthy = bool(data.get(field))
            else_brace, end_brace = _find_block_bounds(source, brace)
            if end_brace is None:
                out.append(source[brace:])
                break
            content_start = close + 2
            if else_brace is not None:
                true_text = source[content_start:else_brace]
                false_text = source[tag_end(else_brace) : end_brace]
            else:
                true_text = source[content_start:end_brace]
                false_text = ""
            out.append(_render_go_template(true_text if truthy else false_text, data))
            i = tag_end(end_brace)
        elif raw in ("else", "end"):
            # handled by parent block scan; ignore stray tags
            i = close + 2
        else:
            field = raw[1:].strip() if raw.startswith(".") else raw.strip()
            out.append(str(data.get(field, raw)))
            i = close + 2
    return "".join(out)


def _render_organization_invitation(
    organization_name: str, invite_link: str, temp_password: str = ""
) -> str:
    """Render the organization invitation HTML."""
    return _render_template(
        "organization_invitation.html",
        {
            "OrganizationName": organization_name,
            "InviteLink": invite_link,
            "TempPassword": temp_password,
        },
    )


# ---------------------------------------------------------------- providers


def _is_valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match(address.strip()))


def send_via_brevo(to_email: str, from_email: str, subject: str, html_content: str) -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("brevo configuration is incomplete (BREVO_API_KEY missing)")
    if not from_email:
        raise RuntimeError("brevo sender email address is not configured")

    payload = {
        "sender": {"email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=data,
        headers={"Content-Type": "application/json", "api-key": BREVO_API_KEY},
        method="POST",
    )
    _execute(req)


def send_via_resend(to_email: str, from_email: str, subject: str, html_content: str) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("resend configuration is incomplete (RESEND_API_KEY missing)")
    if not from_email:
        raise RuntimeError("resend sender email address is not configured")

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + RESEND_API_KEY},
        method="POST",
    )
    _execute(req)


def _execute(req: urllib_request.Request) -> None:
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            status = resp.status
    except urllib_error.HTTPError as exc:
        status = exc.code
        # drain body so the socket is reusable before raising
        try:
            exc.read()
        except Exception:
            pass
        raise RuntimeError(f"{req.full_url.split('/')[2]} api returned status {status}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"network error contacting mail provider: {exc}") from exc
    if status >= 400:
        raise RuntimeError(f"mail provider api returned status {status}")


def send_email(to_email: str, subject: str, html_content: str) -> None:
    """Primary: Brevo API; fallback: Resend API."""
    brevo_from = BREVO_FROM_EMAIL or RESEND_FROM_EMAIL
    brevo_err: Optional[Exception] = None
    try:
        send_via_brevo(to_email, brevo_from, subject, html_content)
        return
    except Exception as exc:  # noqa: BLE001
        brevo_err = exc

    resend_from = RESEND_FROM_EMAIL or brevo_from
    try:
        send_via_resend(to_email, resend_from, subject, html_content)
        return
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"brevo primary failed: {brevo_err}; resend fallback failed: {exc}"
        ) from exc


def send_organization_invitation(
    email: str,
    organization_name: str,
    role_name: str,
    invite_link: str,
    temporary_password: str = "",
) -> None:
    """Send an organization invitation email."""
    if not _is_valid_email(email):
        raise ValueError(f"invalid recipient email address: {email}")

    rendered_html = _render_organization_invitation(
        organization_name, invite_link, temporary_password
    )

    # No providers configured yet -> log instead of throwing so the invite
    # flow still completes while credentials are being provisioned.
    if not (BREVO_API_KEY or RESEND_API_KEY):
        logger.warning(
            "EMAIL NOT SENT (no provider configured) — organization invitation",
            extra={
                "email": email,
                "organization_name": organization_name,
                "role_name": role_name,
                "invite_link": invite_link,
                "temporary_password_provided": bool(temporary_password),
            },
        )
        return

    log = {"email": email, "organization_name": organization_name, "role_name": role_name}
    try:
        send_email(email, "Organization invitation", rendered_html)
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to send organization invitation", extra=log, exc_info=exc)
        raise
    logger.info("organization invitation sent", extra=log)
