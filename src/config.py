import os
import logging
from dotenv import load_dotenv


load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


from src.utils.setting import get_settings

_settings = get_settings()

JWT_SECRET_KEY = _settings.jwt_secret_key
JWT_ALGORITHM = _settings.jwt_algorithm
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = _settings.jwt_access_token_expire_minutes
JWT_REFRESH_TOKEN_EXPIRE_DAYS = _settings.jwt_refresh_token_expire_days


# Email provider config (Brevo primary, Resend fallback).
# Leave empty until credentials are provisioned.
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_FROM_EMAIL = os.getenv("BREVO_FROM_EMAIL", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "")

# CORS configuration
# Enforce explicit origins for browser clients to prevent cross-origin credential leaks.
# Native mobile apps (Flutter/React Native/iOS/Android) do not send an Origin header and are not restricted by browser CORS.
# For hybrid/Capacitor/Ionic mobile apps, capacitor://localhost and http://localhost are included.
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "https://workpilot-frontend-1.onrender.com",
    "https://workpilot-frontend-jo7y.onrender.com",
    "capacitor://localhost",
    "http://localhost",
]

CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", getattr(_settings, "cors_origins", "")).strip()
if CORS_ORIGINS_RAW:
    env_origins = [orig.strip() for orig in CORS_ORIGINS_RAW.split(",") if orig.strip() and orig.strip() != "*"]
    CORS_ORIGINS = list(dict.fromkeys(DEFAULT_CORS_ORIGINS + env_origins))
else:
    CORS_ORIGINS = DEFAULT_CORS_ORIGINS

# AI Assistant configuration
AI_ENABLED = os.getenv("AI_ENABLED", "").lower() in ("true", "1", "yes")
AI_PROVIDER = os.getenv("AI_PROVIDER", "").lower()
AI_MODEL = os.getenv("AI_MODEL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_BASE_URL = os.getenv("AI_BASE_URL", "")
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE")) if os.getenv("AI_TEMPERATURE") else 0.2
AI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS")) if os.getenv("AI_REQUEST_TIMEOUT_SECONDS") else 30.0
AI_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS")) if os.getenv("AI_MAX_OUTPUT_TOKENS") else 1200
AI_MAX_TOOL_ROUNDS = int(os.getenv("AI_MAX_TOOL_ROUNDS")) if os.getenv("AI_MAX_TOOL_ROUNDS") else 3
AI_ENABLE_WRITE_TOOLS = os.getenv("AI_ENABLE_WRITE_TOOLS", "").lower() in ("true", "1", "yes")