from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and backend .env."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    app_name: str = "Autonomous Meeting Intelligence Platform"
    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    public_base_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices("AMIP_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"),
    )

    database_path: Path = PROJECT_ROOT / "data" / "amip.sqlite3"

    gemini_api_key: SecretStr | None = None
    assemblyai_api_key: SecretStr | None = None
    huggingface_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HF_TOKEN", "HUGGINGFACE_TOKEN"),
    )

    smtp_host: str | None = Field(
        default=None, validation_alias=AliasChoices("SMTP_SERVER", "SMTP_HOST")
    )
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str | None = Field(
        default=None, validation_alias=AliasChoices("SMTP_USER", "SMTP_USERNAME")
    )
    smtp_password: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("SMTP_PASS", "SMTP_PASSWORD")
    )
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True

    reminders_enabled: bool = True
    reminders_interval_minutes: int = Field(default=60, ge=5, le=1_440)
    reminder_timezone: str = "UTC"

    frontend_base_url: str = "http://localhost:5173"
    calendar_token_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CALENDAR_TOKEN_ENCRYPTION_KEY", "AMIP_TOKEN_ENCRYPTION_KEY"),
    )
    google_calendar_client_id: str | None = None
    google_calendar_client_secret: SecretStr | None = None
    microsoft_calendar_client_id: str | None = None
    microsoft_calendar_client_secret: SecretStr | None = None
    microsoft_calendar_tenant: str = "common"

    github_token: SecretStr | None = None

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @field_validator("database_path", mode="before")
    @classmethod
    def normalize_database_path(cls, value: object) -> Path:
        """Expand a user-home marker and normalize the configured database path."""
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise ValueError("database_path must be a filesystem path or string")

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("public_base_url must be an HTTP(S) origin without credentials or path")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("public_base_url must use HTTPS outside localhost")
        return normalized


settings = Settings()


SMTP_SERVER = settings.smtp_host
SMTP_PORT = settings.smtp_port
SMTP_USER = settings.smtp_username
SMTP_PASS = settings.smtp_password
SMTP_FROM_EMAIL = settings.smtp_from_email
SMTP_USE_TLS = settings.smtp_use_tls
