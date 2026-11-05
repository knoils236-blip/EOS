from typing import ClassVar

from litestar import Controller, get, put
from pydantic import BaseModel, Field, field_validator

from eos.auth.authorization import require_superuser
from eos.optimization.openai_connection import AIProtocol, OpenAIConnection
from eos.web_api.exception_handling import APIError


class SaveAISettingsRequest(BaseModel):
    protocol: AIProtocol
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: str = Field("", max_length=8192)

    @field_validator("base_url", "model")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("api_key")
    @classmethod
    def trim_api_key(cls, value: str) -> str:
        return value.strip()


def _settings_response(connection: OpenAIConnection) -> dict:
    return {
        "protocol": connection.protocol,
        "base_url": connection.base_url,
        "model": connection.model,
        "timeout_s": connection.timeout_s,
        "key_configured": bool(connection.api_key.get_secret_value()),
        "configured": connection.configured,
    }


class AISettingsController(Controller):
    path = "/ai-settings"
    guards: ClassVar = [require_superuser()]

    @get("/")
    async def get_ai_settings(self) -> dict:
        return _settings_response(OpenAIConnection())

    @put("/")
    async def save_ai_settings(self, data: SaveAISettingsRequest) -> dict:
        current = OpenAIConnection()
        api_key = data.api_key or current.api_key.get_secret_value()
        if not api_key:
            raise APIError(status_code=400, detail="首次保存 AI 设置时必须提供 API Key")
        connection = OpenAIConnection(
            protocol=data.protocol,
            base_url=data.base_url,
            model=data.model,
            api_key=api_key,
            timeout_s=current.timeout_s,
        )
        connection.save()
        return _settings_response(connection)
