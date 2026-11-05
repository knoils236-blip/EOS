"""AI-drafted protocols: turn an experimental goal into a validated protocol.yml for a scientist to review."""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field

FALLBACK_DRAFT_MODEL = "claude-agent-sdk:sonnet"


def default_draft_model() -> str:
    """Use the server AI settings when configured, else Claude through the Agent SDK."""
    from eos.optimization.openai_connection import OpenAIConnection  # noqa: PLC0415

    connection = OpenAIConnection()
    return connection.model_ref if connection.configured else FALLBACK_DRAFT_MODEL


MAX_REPAIRS = 2

SYSTEM_PROMPT = """\
You design EOS laboratory automation protocols. A protocol is a DAG of tasks, written as protocol.yml:

type: <the given protocol type>
desc: <one sentence>
labs: [<names of the labs whose devices the tasks use>]
tasks:
  - name: <unique snake_case name>
    type: <a task type exactly as listed>
    desc: <what this step does in the experiment>
    duration: <expected seconds>
    dependencies: [<names of tasks that must finish first>]
    devices:
      <device slot of the task type>: {lab_name: <lab>, name: <device of the slot's type in that lab>}
      <device slot>: <earlier_task>.<slot>     # reuse the device an earlier task used
    parameters:
      <input name>: <literal value of the input's type>
      <input name>: <earlier_task>.<output name>   # the task must depend on <earlier_task>
      <input name>: eos_dynamic                    # left for the submitter or an optimizer to choose

Rules:
- Use only the listed task types, labs and devices. Assign every non-optional device slot.
- Omit an input to keep its default; give every input without a default a value or eos_dynamic.
- Prefer eos_dynamic for the values the goal asks to vary or optimize.
- When a device lists scene operations, build its tasks from them: copy an operation's task type and parameters
  (change only parameter values, within their ranges), and assign the operation's device slot to that device.
- Follow each scene's notes. They state the initial state, required order and single-use resources, and the
  validator rejects plans that break them.
- Put only the YAML in `yaml`, without code fences. Put assumptions and values the scientist should check in `notes`.
"""


class ProtocolDraft(BaseModel):
    yaml: str
    notes: str = ""
    valid: bool = False
    errors: list[dict[str, Any]] = Field(default_factory=list)
    attempts: int = 0


class _ModelOutput(BaseModel):
    yaml: str = Field(description="The complete protocol.yml content")
    notes: str = Field(description="Short notes for the scientist: assumptions, values to check, missing capabilities")


Validator = Callable[[str], Awaitable[dict[str, Any]]]


class ProtocolDrafter:
    def __init__(self, model: str | None = None):
        # Imported here so the web API does not load the AI and optimization stacks at startup
        from pydantic_ai import Agent, PromptedOutput  # noqa: PLC0415

        from eos.optimization.beacon_ai_agent import _resolve_model, _set_api_key, _validate_model  # noqa: PLC0415

        model = model or default_draft_model()
        _validate_model(model)
        _set_api_key(model, None)
        resolved_model, _ = _resolve_model(model, None)
        # OpenAI-compatible reasoning models can reject forced tool calls; native providers use structured output.
        output_type = PromptedOutput(_ModelOutput) if model.startswith("openai:") else _ModelOutput
        self._agent = Agent(model=resolved_model, system_prompt=SYSTEM_PROMPT, output_type=output_type, retries=2)

    async def draft(
        self,
        goal: str,
        protocol_type: str,
        labs: dict[str, dict[str, Any]],
        task_specs: dict[str, dict[str, Any]],
        validate: Validator,
        scenes: Sequence[dict[str, Any]] = (),
    ) -> ProtocolDraft:
        """Draft a protocol, then feed validation errors back to the model up to MAX_REPAIRS times."""
        result = await self._agent.run(_describe_request(goal, protocol_type, labs, task_specs, scenes))
        for attempt in range(1, MAX_REPAIRS + 2):
            output = result.output
            check = await validate(output.yaml)
            if check["valid"] or attempt > MAX_REPAIRS:
                break
            errors = "\n".join(f"- {e['message']}" for e in check["errors"])
            result = await self._agent.run(
                f"The protocol failed validation:\n{errors}\nReturn the corrected complete protocol.",
                message_history=result.all_messages(),
            )
        return ProtocolDraft(
            yaml=output.yaml, notes=output.notes, valid=check["valid"], errors=check["errors"], attempts=attempt
        )


def _describe_request(
    goal: str,
    protocol_type: str,
    labs: dict[str, dict[str, Any]],
    task_specs: dict[str, dict[str, Any]],
    scenes: Sequence[dict[str, Any]] = (),
) -> str:
    lab_lines = [
        f"- {name}: " + ", ".join(f"{device} ({spec['type']})" for device, spec in lab["devices"].items())
        for name, lab in labs.items()
    ]
    task_lines = [json.dumps(_compact_task_spec(spec), ensure_ascii=False) for spec in task_specs.values()]
    request = (
        f"Goal: {goal}\n\nProtocol type: {protocol_type}\n\nLabs and their devices (name (type)):\n"
        + "\n".join(lab_lines)
        + "\n\nTask types (JSON):\n"
        + "\n".join(task_lines)
    )
    for scene in scenes:
        operations = [json.dumps(_compact_preset(preset), ensure_ascii=False) for preset in scene["presets"]]
        notes = "\n".join(f"- {note}" for note in scene.get("notes", []))
        request += (
            f"\n\nScene '{scene['label']}' on device {scene['device_name']} of lab {scene['lab_name']}."
            + (f"\nScene notes:\n{notes}" if notes else "")
            + "\nScene operations (JSON):\n"
            + "\n".join(operations)
        )
    return request


def _compact_preset(preset: dict[str, Any]) -> dict[str, Any]:
    """An operation's meaning, its task template and the ranges of its adjustable parameters."""
    compact = {
        "id": preset["id"],
        "label": preset["label"],
        "group": preset.get("group"),
        "description": preset.get("description"),
        "device_slot": preset.get("device_slot"),
        "task": {key: preset["task"][key] for key in ("type", "parameters") if key in preset["task"]},
        "parameter_ranges": {
            name: {key: spec[key] for key in ("min", "max", "choices", "unit") if spec.get(key) is not None}
            for name, spec in (preset.get("parameter_specs") or {}).items()
        },
    }
    return {k: v for k, v in compact.items() if v}


def _compact_task_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Keep what the model needs to wire a task, dropping empty and null fields."""

    def clean(fields: dict[str, Any]) -> dict[str, Any]:
        return {name: {k: v for k, v in field.items() if v is not None} for name, field in fields.items()}

    compact = {
        "type": spec["type"],
        "desc": spec.get("desc"),
        "devices": spec.get("devices"),
        "inputs": clean(spec.get("input_parameters") or {}),
        "outputs": clean(spec.get("output_parameters") or {}),
        "resources": spec.get("input_resources"),
    }
    return {k: v for k, v in compact.items() if v}
