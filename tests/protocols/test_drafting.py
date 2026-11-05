import json

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from eos.protocols.drafting import MAX_REPAIRS, ProtocolDrafter

LABS = {"mult_lab": {"devices": {"multiplier": {"type": "multiplier", "computer": "eos_computer"}}}}
TASK_SPECS = {
    "multiplication": {
        "type": "Multiplication",
        "desc": "Multiply a number by a factor.",
        "devices": {"multiplier": {"type": "multiplier", "optional": False}},
        "input_parameters": {"number": {"type": "int", "value": None}, "factor": {"type": "int", "value": None}},
        "output_parameters": {"product": {"type": "int", "unit": None}},
    }
}
VALID = "type: draft\ndesc: d\nlabs: [mult_lab]\ntasks: []\n"


def _model(yamls: list[str], prompts: list[str]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts.append(
            next(
                p.content for m in reversed(messages) for p in getattr(m, "parts", []) if isinstance(p, UserPromptPart)
            )
        )
        output = {"yaml": yamls[len(prompts) - 1], "notes": "check x"}
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])
        return ModelResponse(parts=[TextPart(json.dumps(output))])  # prompted (JSON text) output

    return FunctionModel(respond)


async def _validate(text: str) -> dict:
    return {"valid": text == VALID, "errors": [] if text == VALID else [{"task": None, "message": "bad device"}]}


class TestProtocolDrafter:
    @pytest.mark.parametrize("model", ["claude-agent-sdk:sonnet", "openai:any-model"])
    async def test_repairs_until_valid(self, model, monkeypatch):
        monkeypatch.setenv("EOS_AI_BASE_URL", "http://127.0.0.1:9/v1")  # never contacted: the model is overridden
        monkeypatch.setenv("EOS_AI_API_KEY", "test-key")
        prompts: list[str] = []
        drafter = ProtocolDrafter(model)
        with drafter._agent.override(model=_model(["broken", VALID], prompts)):
            draft = await drafter.draft("multiply 4 by 16", "draft", LABS, TASK_SPECS, _validate)

        assert draft.valid
        assert (draft.yaml, draft.notes, draft.attempts) == (VALID, "check x", 2)
        assert "mult_lab: multiplier (multiplier)" in prompts[0]
        assert '"type": "Multiplication"' in prompts[0]
        assert "bad device" in prompts[1]

    async def test_returns_last_draft_with_errors_after_max_repairs(self):
        prompts: list[str] = []
        drafter = ProtocolDrafter()
        with drafter._agent.override(model=_model(["broken"] * (MAX_REPAIRS + 1), prompts)):
            draft = await drafter.draft("goal", "draft", LABS, TASK_SPECS, _validate)

        assert not draft.valid
        assert draft.errors == [{"task": None, "message": "bad device"}]
        assert len(prompts) == MAX_REPAIRS + 1

    async def test_scene_operations_and_notes_reach_the_model(self):
        scene = {
            "lab_name": "mult_lab",
            "device_name": "multiplier",
            "label": "Multiplier bench",
            "notes": ["Warm up before multiplying."],
            "presets": [
                {
                    "id": "double",
                    "label": "Double a number",
                    "group": "operations",
                    "description": "Multiply by two",
                    "device_slot": "multiplier",
                    "parameter_specs": {"number": {"type": "integer", "default": 1, "min": 0, "max": 9, "label": "n"}},
                    "task": {"type": "Multiplication", "parameters": {"factor": 2}, "timeout": 60},
                }
            ],
        }
        prompts: list[str] = []
        drafter = ProtocolDrafter()
        with drafter._agent.override(model=_model([VALID], prompts)):
            await drafter.draft("double 4", "draft", LABS, TASK_SPECS, _validate, [scene])

        assert "Scene 'Multiplier bench' on device multiplier of lab mult_lab." in prompts[0]
        assert "- Warm up before multiplying." in prompts[0]
        operation = json.loads(prompts[0].rsplit("\n", 1)[1])
        assert operation["task"] == {"type": "Multiplication", "parameters": {"factor": 2}}
        assert operation["parameter_ranges"] == {"number": {"min": 0, "max": 9}}
