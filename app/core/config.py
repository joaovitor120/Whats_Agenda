from functools import lru_cache
from ipaddress import ip_network
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    public_base_url: str = "http://localhost:8000"
    default_timezone: str = "America/Sao_Paulo"
    database_url: str = "sqlite+aiosqlite:///./whatsagenda.db"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    llm_provider: Literal["openai", "anthropic"] = "openai"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    llm_max_output_tokens: int = Field(default=2048, ge=256, le=4096)
    audio_transcription_provider: Literal["openai", "local"] = "openai"
    local_transcription_url: str = "http://transcription:8001"
    local_transcription_api_key: str = ""
    evolution_api_url: str = "http://localhost:8080"
    evolution_webhook_base_url: str = ""
    evolution_api_key: str = ""
    evolution_instance_name: str = "whats-agenda"
    evolution_instance_token: str = ""
    evolution_webhook_secret: str = ""
    whatsapp_allowed_contacts: dict[str, str] = Field(default_factory=dict)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    microsoft_redirect_uri: str = ""
    token_encryption_key: str = ""
    admin_api_key: str = ""
    default_day_start: str = "05:00"
    default_day_end: str = "23:00"
    reminder_max_lateness_minutes: int = Field(default=30, ge=1, le=120)
    fake_calendar: bool = True
    fake_llm: bool = True
    fake_evolution: bool = True
    audio_enabled: bool = False
    max_audio_bytes: int = Field(default=20_000_000, ge=1024, le=20_000_000)
    max_audio_seconds: int = Field(default=600, ge=1, le=600)
    max_text_chars: int = Field(default=4000, ge=100, le=16000)
    webhook_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    admin_allowed_networks: str = "127.0.0.1/32,::1/128"
    rate_limit_enabled: bool = True
    messages_per_minute: int = Field(default=20, ge=1, le=100)
    messages_per_day: int = Field(default=200, ge=1)
    llm_requests_per_day: int = Field(default=100, ge=1)
    audio_seconds_per_day: int = Field(default=1800, ge=1)
    retention_days: int = Field(default=30, ge=1, le=365)

    def whatsapp_allowed(self, phone: str) -> bool:
        # Exact authenticated phone identity only; never authorize by display name or suffix.
        return phone in self.whatsapp_allowed_contacts

    @model_validator(mode="after")
    def validate_runtime(self):
        from datetime import time

        ZoneInfo(self.default_timezone)
        if time.fromisoformat(self.default_day_start) >= time.fromisoformat(self.default_day_end):
            raise ValueError("Working hours must end after they start")
        for address in (
            self.public_base_url,
            self.evolution_api_url,
            self.evolution_webhook_base_url or self.public_base_url,
            self.local_transcription_url,
        ):
            parsed = urlsplit(address)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Integration origins must be valid HTTP(S) URLs without embedded credentials"
                )
        for network in self.admin_allowed_networks.split(","):
            ip_network(network.strip())
        import re

        for phone, name in self.whatsapp_allowed_contacts.items():
            if not re.fullmatch(r"[1-9][0-9]{7,14}", phone) or not 1 <= len(name.strip()) <= 120:
                raise ValueError(
                    "WHATSAPP_ALLOWED_CONTACTS requires international digits-only phones and names of 1-120 characters"
                )
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.evolution_instance_name):
            raise ValueError("Invalid Evolution instance name")
        if not re.fullmatch(r"[a-zA-Z0-9.-]{1,100}", self.microsoft_tenant):
            raise ValueError("Invalid Microsoft tenant")
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        llm_key = (
            self.anthropic_api_key if self.llm_provider == "anthropic" else self.openai_api_key
        )
        if not self.fake_llm and not llm_key:
            raise ValueError("Configure the API key for the selected LLM provider")
        if self.audio_enabled:
            if self.audio_transcription_provider == "openai" and not self.openai_api_key:
                raise ValueError("OpenAI audio transcription requires OPENAI_API_KEY")
            if (
                self.audio_transcription_provider == "local"
                and len(self.local_transcription_api_key) < 32
            ):
                raise ValueError("Local transcription requires a secret of at least 32 characters")
        if self.app_env == "production":
            if any((self.fake_calendar, self.fake_llm, self.fake_evolution)):
                raise ValueError("Fake integrations are forbidden in production")
            for name in ("app_secret_key", "evolution_webhook_secret", "admin_api_key"):
                value = getattr(self, name)
                if len(value) < 32 or "replace" in value or "development" in value:
                    raise ValueError(
                        f"{name} requires a unique random secret of at least 32 characters"
                    )
            if len({self.app_secret_key, self.evolution_webhook_secret, self.admin_api_key}) != 3:
                raise ValueError("Use separate secrets for each trust boundary")
            if not self.token_encryption_key:
                raise ValueError("TOKEN_ENCRYPTION_KEY is required")
            Fernet(self.token_encryption_key.encode())
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("Production requires PostgreSQL")
            if not llm_key or not (self.evolution_api_key or self.evolution_instance_token):
                raise ValueError("Production integration credentials are required")
            if not self.rate_limit_enabled:
                raise ValueError("Production quotas cannot be disabled")
            if urlsplit(self.public_base_url).scheme != "https":
                raise ValueError("PUBLIC_BASE_URL must use HTTPS")
            if not (self.google_client_id or self.microsoft_client_id):
                raise ValueError("Configure at least one real calendar provider")
            if any(
                ip_network(net.strip()).prefixlen == 0
                for net in self.admin_allowed_networks.split(",")
            ):
                raise ValueError("The administrative allowlist cannot include the entire Internet")
            for provider in ("google", "microsoft"):
                if getattr(self, f"{provider}_client_id"):
                    if not getattr(self, f"{provider}_client_secret"):
                        raise ValueError("Configured OAuth clients require their client secret")
                    expected = f"{self.public_base_url.rstrip('/')}/oauth/{provider}/callback"
                    if getattr(self, f"{provider}_redirect_uri") != expected:
                        raise ValueError("OAuth redirect URI must exactly match the fixed callback")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
