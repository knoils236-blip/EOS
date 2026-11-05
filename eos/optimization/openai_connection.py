"""Persistent AI connection shared by decision reviews, drafting, and Beacon."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from openai import AsyncOpenAI
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, InitSettingsSource, SettingsConfigDict

AIProtocol = Literal["openai", "anthropic"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AI_SETTINGS_FILE = _PROJECT_ROOT / ".eos/ai-settings.json"
_SETTINGS_FILE_ENV = "EOS_AI_SETTINGS_FILE"
_MAX_API_KEY_LENGTH = 8192


def ai_settings_path() -> Path:
    """Return the current settings path, including an explicit environment override."""
    path = Path(os.environ.get(_SETTINGS_FILE_ENV, DEFAULT_AI_SETTINGS_FILE)).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def _read_ai_settings() -> dict[str, Any]:
    path = ai_settings_path()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        settings = json.load(file)
    if not isinstance(settings, dict):
        raise ValueError("AI settings file must contain a JSON object")
    return settings


def _write_ai_settings(settings: dict[str, Any]) -> None:
    """Atomically replace the private settings file."""
    path = ai_settings_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            descriptor = -1
            json.dump(settings, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


class OpenAIConnection(BaseSettings):
    """Validated OpenAI-compatible or Anthropic connection.

    The name is retained for compatibility with existing imports. The persisted server setting takes
    precedence over ``EOS_AI_*``; explicitly passed constructor values take precedence over both.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="EOS_AI_", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )

    protocol: AIProtocol = "openai"
    base_url: str = Field("", max_length=2048)
    api_key: SecretStr = SecretStr("")
    model: str = Field("", max_length=256)
    timeout_s: float = Field(60, gt=0, le=300)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ) -> tuple[Any, ...]:
        return (
            init_settings,
            InitSettingsSource(settings_cls, _read_ai_settings()),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AI base URL must be an HTTP(S) API base URL without credentials or query parameters")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        if len(raw) > _MAX_API_KEY_LENGTH:
            raise ValueError("AI API key must contain at most 8192 characters")
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> "OpenAIConnection":
        terminal_paths = {
            "openai": "/chat/completions",
            "anthropic": "/messages",
        }
        if self.base_url.endswith(terminal_paths[self.protocol]):
            raise ValueError(f"Use the {self.protocol} API base URL without the terminal chat endpoint")
        return self

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key.get_secret_value() and self.model)

    @property
    def model_ref(self) -> str:
        return f"{self.protocol}:{self.model}"

    def client(self) -> AsyncOpenAI:
        if self.protocol != "openai" or not self.configured:
            raise ValueError("Configure an OpenAI base URL, API key, and model on the server")
        return AsyncOpenAI(
            base_url=self.base_url, api_key=self.api_key.get_secret_value(), timeout=self.timeout_s, max_retries=0
        )

    def anthropic_client(self) -> Any:
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        if self.protocol != "anthropic" or not self.configured:
            raise ValueError("Configure an Anthropic base URL, API key, and model on the server")
        return AsyncAnthropic(
            base_url=self.base_url,
            api_key=self.api_key.get_secret_value(),
            timeout=self.timeout_s,
            max_retries=0,
        )

    def save(self) -> None:
        if not self.configured:
            raise ValueError("AI settings require a base URL, API key, and model")
        _write_ai_settings(
            {
                "protocol": self.protocol,
                "base_url": self.base_url,
                "api_key": self.api_key.get_secret_value(),
                "model": self.model,
                "timeout_s": self.timeout_s,
            }
        )
