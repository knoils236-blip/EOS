import json
import os

from anthropic import AsyncAnthropic
import httpx
import httpx2
from litestar import Litestar
from litestar.testing import AsyncTestClient
from openai import AsyncOpenAI
import pytest

from eos.campaigns.campaign_optimizer_manager import _apply_ai_connection
from eos.optimization.beacon_ai_agent import _resolve_model
from eos.optimization.openai_connection import DEFAULT_AI_SETTINGS_FILE, OpenAIConnection, ai_settings_path
from eos.protocols.drafting import default_draft_model
from eos.web_api.controllers.ai_settings_controller import AISettingsController
from eos.web_api.controllers.decision_controller import _review_with_ai


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ai-settings.json"
    monkeypatch.setenv("EOS_AI_SETTINGS_FILE", str(path))
    for name in ("EOS_AI_PROTOCOL", "EOS_AI_BASE_URL", "EOS_AI_API_KEY", "EOS_AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return path


def test_persisted_settings_are_private_and_override_environment(settings_file, monkeypatch):
    OpenAIConnection(
        protocol="anthropic",
        base_url="https://anthropic.example",
        model="claude-test",
        api_key="persisted-secret",
    ).save()

    assert settings_file.parent.stat().st_mode & 0o777 == 0o700
    assert settings_file.stat().st_mode & 0o777 == 0o600
    assert not list(settings_file.parent.glob(".ai-settings.json.*"))

    monkeypatch.setenv("EOS_AI_PROTOCOL", "openai")
    monkeypatch.setenv("EOS_AI_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("EOS_AI_API_KEY", "environment-secret")
    monkeypatch.setenv("EOS_AI_MODEL", "environment-model")

    connection = OpenAIConnection()
    assert connection.protocol == "anthropic"
    assert connection.base_url == "https://anthropic.example"
    assert connection.model == "claude-test"
    assert connection.api_key.get_secret_value() == "persisted-secret"


def test_default_settings_path_is_absolute(monkeypatch):
    monkeypatch.delenv("EOS_AI_SETTINGS_FILE", raising=False)

    assert ai_settings_path() == DEFAULT_AI_SETTINGS_FILE
    assert ai_settings_path().is_absolute()


async def test_api_hides_key_and_empty_save_keeps_existing_key(settings_file):
    app = Litestar(route_handlers=[AISettingsController])
    async with AsyncTestClient(app=app) as client:
        response = await client.put(
            "/ai-settings/",
            json={
                "protocol": "anthropic",
                "base_url": "https://anthropic.example",
                "model": "claude-test",
                "api_key": "persisted-secret",
            },
        )
        assert response.status_code == 200
        assert response.json()["key_configured"] is True

        response = await client.get("/ai-settings/")
        assert response.status_code == 200
        assert response.json()["key_configured"] is True
        assert "persisted-secret" not in response.text

        response = await client.put(
            "/ai-settings/",
            json={
                "protocol": "anthropic",
                "base_url": "https://anthropic.example",
                "model": "claude-test-2",
                "api_key": "",
            },
        )
        assert response.status_code == 200
        assert response.json()["key_configured"] is True

    assert json.loads(settings_file.read_text())["api_key"] == "persisted-secret"
    assert ai_settings_path() == settings_file
    assert OpenAIConnection().model == "claude-test-2"


def test_connection_validates_provider_endpoint_and_field_bounds(settings_file):
    with pytest.raises(ValueError, match="terminal chat endpoint"):
        OpenAIConnection(
            protocol="openai",
            base_url="https://service.example/v1/chat/completions",
            model="model",
            api_key="secret",
        )
    with pytest.raises(ValueError, match="terminal chat endpoint"):
        OpenAIConnection(
            protocol="anthropic",
            base_url="https://api.anthropic.com/v1/messages",
            model="model",
            api_key="secret",
        )
    with pytest.raises(ValueError, match="at most 256"):
        OpenAIConnection(model="x" * 257)
    with pytest.raises(ValueError, match="at most 8192"):
        OpenAIConnection(api_key=os.urandom(8193))


def test_anthropic_settings_reach_defaults_and_beacon(settings_file):
    OpenAIConnection(
        protocol="anthropic",
        base_url="https://anthropic.example",
        model="claude-test",
        api_key="secret",
    ).save()

    assert default_draft_model() == "anthropic:claude-test"
    model, code_execution = _resolve_model("anthropic:claude-test", None)
    assert type(model).__name__ == "AnthropicModel"
    assert code_execution is False

    constructor_args = {"ai_model": "claude-agent-sdk:sonnet"}
    _apply_ai_connection(constructor_args, None)
    assert constructor_args == {
        "ai_model": "anthropic:claude-test",
        "ai_base_url": "https://anthropic.example",
        "ai_api_key": "secret",
    }


@pytest.mark.parametrize("model", ["claude-agent-sdk:sonnet", "ollama:qwen3:8b"])
def test_explicit_legacy_beacon_models_do_not_read_ai_settings(tmp_path, monkeypatch, model):
    settings_file = tmp_path / "ai-settings.json"
    settings_file.write_text("{not valid json")
    monkeypatch.setenv("EOS_AI_SETTINGS_FILE", str(settings_file))

    constructor_args = {"ai_model": model}
    _apply_ai_connection(constructor_args, {"ai_model": model})

    assert constructor_args == {"ai_model": model}


async def test_review_uses_anthropic_messages(settings_file, monkeypatch):
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["content-type"].startswith("application/json")
        body = json.loads(request.content)
        payload = json.loads(body["messages"][0]["content"])
        assert body["model"] == "claude-test"
        assert body["max_tokens"] == 4096
        assert payload == {"question": "下一步？", "evidence": {"rounds": []}}
        assert "anthropic-secret" not in request.content.decode()
        return httpx2.Response(
            200,
            json={
                "id": "msg-local",
                "type": "message",
                "role": "assistant",
                "model": "claude-test",
                "content": [
                    {"type": "text", "text": "观测事实明确。"},
                    {"type": "text", "text": "建议继续验证。"},
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    def client(self):
        return AsyncAnthropic(
            base_url=self.base_url,
            api_key=self.api_key.get_secret_value(),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )

    monkeypatch.setattr(OpenAIConnection, "anthropic_client", client)
    connection = OpenAIConnection(
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-test",
        api_key="anthropic-secret",
    )

    text = await _review_with_ai(connection, {"question": "下一步？", "evidence": {"rounds": []}})

    assert text == "观测事实明确。建议继续验证。"


async def test_review_uses_openai_chat_completions(settings_file, monkeypatch):
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openai.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer openai-secret"
        assert request.headers["content-type"].startswith("application/json")
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        assert body["model"] == "gpt-test"
        assert payload == {"question": "下一步？", "evidence": {"rounds": []}}
        assert "openai-secret" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": "chat-local",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OpenAI 本地桩响应。"},
                    }
                ],
            },
        )

    def client(self):
        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key.get_secret_value(),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr(OpenAIConnection, "client", client)
    connection = OpenAIConnection(
        protocol="openai",
        base_url="https://openai.example/v1",
        model="gpt-test",
        api_key="openai-secret",
    )

    text = await _review_with_ai(connection, {"question": "下一步？", "evidence": {"rounds": []}})

    assert text == "OpenAI 本地桩响应。"
