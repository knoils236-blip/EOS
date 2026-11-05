import json
from unittest.mock import AsyncMock

from bofire.data_models.domain.api import Domain
from bofire.data_models.features.api import ContinuousInput, ContinuousOutput
from bofire.data_models.objectives.api import MinimizeObjective
import httpx
from litestar import Litestar
from litestar.di import Provide
from litestar.testing import AsyncTestClient
from openai import AsyncOpenAI
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from eos.optimization.beacon_ai_agent import BeaconAIAgent
from eos.optimization.openai_connection import OpenAIConnection
from eos.web_api.controllers import decision_controller as module
from eos.web_api.controllers.decision_controller import DecisionController
from eos.web_api.exception_handling import general_exception_handler


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setenv("EOS_AI_BASE_URL", "http://127.0.0.1:65500/v1")
    monkeypatch.setenv("EOS_AI_API_KEY", "local-stub-secret")
    monkeypatch.setenv("EOS_AI_MODEL", "local-stub-model")
    return OpenAIConnection()


def test_configuration_masks_credentials_and_rejects_full_completion_url(connection):
    assert connection.configured
    assert "local-stub-secret" not in connection.model_dump_json()
    with pytest.raises(ValueError, match="base URL"):
        OpenAIConnection(base_url="http://localhost/v1/chat/completions")
    with pytest.raises(ValueError, match="without credentials or query parameters") as error:
        OpenAIConnection(base_url="https://service.invalid/v1?api_key=local-stub-secret")
    assert "local-stub-secret" not in str(error.value)


async def test_beacon_chat_protocol_retries_invalid_suggestion_without_global_keys(connection, monkeypatch):
    requests = []

    def handle(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer local-stub-secret"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "local-stub-model"
        arguments = {
            "suggestions": [{"parameters": {"x": 200 if len(requests) == 1 else 0.4}}],
            "journal_entry": "在已测量区域附近验证响应。",
        }
        return httpx.Response(
            200,
            json={
                "id": "local-response",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": body["tools"][0]["function"]["name"],
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async with AsyncOpenAI(
        base_url=connection.base_url,
        api_key=connection.api_key.get_secret_value(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(OpenAIConnection, "client", lambda self: client)
        domain = Domain.from_lists(
            inputs=[ContinuousInput(key="x", bounds=(0, 1))],
            outputs=[ContinuousOutput(key="y", objective=MinimizeObjective())],
        )
        agent = BeaconAIAgent(domain, "openai:local-stub-model", None, retries=2)
        frame, reason = await agent.suggest_async(1, [], [], [], total_runs=0)
    assert frame.x.tolist() == [0.4]
    assert len(requests) == 2
    assert reason


async def test_review_only_sends_scientific_evidence_and_returns_no_credentials(connection, monkeypatch):
    evidence = {
        "campaign_name": "test",
        "generated_at": "2026-11-02T00:00:00Z",
        "snapshot_id": "snapshot",
        "rounds": [{"run_name": "run_1", "data_source": "synthetic_model", "files": [{"name": "private-path"}]}],
    }
    monkeypatch.setattr(module, "load_decision", AsyncMock(return_value=evidence))
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "local-stub-model"
        assert "private-path" not in request.content.decode()
        assert "local-stub-secret" not in request.content.decode()
        assert "tools" not in body
        return httpx.Response(
            200,
            json={
                "id": "review",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "run_1 仅有合成数据。"},
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

    async def db():
        async with AsyncSession() as session:
            yield session

    app = Litestar(
        route_handlers=[DecisionController],
        dependencies={"db": Provide(db)},
        exception_handlers={Exception: general_exception_handler},
    )
    async with AsyncTestClient(app=app) as browser:
        status = await browser.get("/campaigns/test/decision")
        assert status.status_code == 200
        assert "local-stub-secret" not in status.text
        response = await browser.post("/campaigns/test/decision/review", json={"question": "下一步？"})
    assert response.status_code == 201, response.text
    assert response.json()["text"] == "run_1 仅有合成数据。"
    assert response.json()["snapshot_id"] == "snapshot"
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [401, 500, "timeout", "unconfigured"])
async def test_review_failure_is_explicit_and_never_echoes_provider_secrets(connection, monkeypatch, failure):
    monkeypatch.setattr(module, "load_decision", AsyncMock(return_value={"rounds": []}))

    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("local-stub-secret", request=request)
        return httpx.Response(failure, json={"error": {"message": "local-stub-secret"}})

    if failure == "unconfigured":
        monkeypatch.setattr(module, "OpenAIConnection", lambda: OpenAIConnection(base_url="", api_key="", model=""))
    else:
        monkeypatch.setattr(
            OpenAIConnection,
            "client",
            lambda self: AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key.get_secret_value(),
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            ),
        )

    async def db():
        async with AsyncSession() as session:
            yield session

    app = Litestar(
        route_handlers=[DecisionController],
        dependencies={"db": Provide(db)},
        exception_handlers={Exception: general_exception_handler},
    )
    async with AsyncTestClient(app=app) as browser:
        response = await browser.post("/campaigns/test/decision/review", json={"question": "下一步？"})
    expected = 409 if failure == "unconfigured" else 504 if failure == "timeout" else 502
    assert response.status_code == expected, response.text
    assert "local-stub-secret" not in response.text
