import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from eos.integrations.sila import SilaClientContext, SilaDevice
from eos.logging.logger import log
from matterix_sim.observations import extract_archive_evidence, recording_settings
from matterix_sim.inventory import consumable_events

SESSION_END_TIMEOUT = 120  # seconds for Isaac Sim to shut down after EndSession
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
CAP_CLOSED_OBJECTS = (
    "DesignD_DesignC_ChromatographyVial2ml_190",
    "DesignD_DesignC_BottleBody500ml_238",
)


def _station_closure_checks(semantics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Require closed caps, released tools, and disposal evidence before ending a station experiment."""

    def check(name: str, actual: Any, expected: Any, passed: bool) -> dict[str, Any]:
        return {"name": name, "actual": actual, "expected": expected, "passed": passed}

    def records(value: Any) -> list[Mapping[str, Any]]:
        return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    def is_tip(name: Any) -> bool:
        return isinstance(name, str) and "_Tip_" in name

    def is_syringe(name: Any) -> bool:
        return isinstance(name, str) and name.startswith("SyringeComponent")

    device = semantics.get("device_state") if isinstance(semantics.get("device_state"), Mapping) else {}
    semantic = device.get("semantic_state") if isinstance(device.get("semantic_state"), Mapping) else {}
    cap_closed = device.get("cap_closed")
    attached = device.get("attached") if isinstance(device.get("attached"), Mapping) else {}
    seated = device.get("seated_tips") if isinstance(device.get("seated_tips"), Mapping) else {}
    grips = device.get("grips") if isinstance(device.get("grips"), Mapping) else {}
    events = records(device.get("operation_events"))
    checks = []

    if not isinstance(cap_closed, Mapping):
        checks.append(check("cap_closed evidence", cap_closed, "object", False))
    else:
        open_caps = sorted(name for name in CAP_CLOSED_OBJECTS if cap_closed.get(name) is not True)
        checks.append(check("caps closed", open_caps, [], not open_caps))

    tip_refs = {
        str(name)
        for name, mounted in {**attached, **seated}.items()
        if mounted is True and is_tip(name)
    }
    tip_refs.update(
        str(event.get("object"))
        for event in events
        if event.get("event") == "grasp" and is_tip(event.get("object"))
    )
    if semantic.get("TipOn") is True:
        tip_refs.add("TipOn")
    released_tip_refs = {
        str(event.get("object"))
        for event in events
        if event.get("event") == "release" and is_tip(event.get("object"))
    }
    tip_released = bool(tip_refs) and tip_refs <= released_tip_refs
    if tip_refs:
        checks.append(
            check("used TIP disposal evidence", bool(tip_released), True, tip_released)
        )
        checks.append(
            check("TipOn", semantic.get("TipOn", "<missing>"), False, semantic.get("TipOn") is False)
        )
        mounted_tips = sorted(
            str(name)
            for name, value in {**attached, **seated}.items()
            if value is True and is_tip(name)
        )
        checks.append(check("TIPs released", mounted_tips, [], not mounted_tips))

    filter_events = [
        event for event in events
        if event.get("event") == "filter_installed"
        and event.get("barrel") == "SyringeComponent1_676"
    ]
    installed_filters = {str(event.get("object")) for event in filter_events if event.get("object")}
    syringe_refs = {
        str(name)
        for name, mounted in attached.items()
        if mounted is True and is_syringe(name)
    }
    syringe_refs.update(
        str(event.get("object"))
        for event in events
        if event.get("event") == "grasp" and is_syringe(event.get("object"))
    )
    syringe_used = bool(syringe_refs or installed_filters)
    syringe_used = syringe_used or semantic.get("SyringeOn") is True
    syringe_used = syringe_used or semantic.get("FilterHeadOn") is True
    released_syringe_refs = {
        str(event.get("object"))
        for event in events
        if event.get("event") == "release" and is_syringe(event.get("object"))
    }
    syringe_released = bool(syringe_refs) and syringe_refs <= released_syringe_refs
    if syringe_used:
        checks.append(
            check("used syringe/filter disposal evidence", syringe_released, True, syringe_released)
        )
        checks.append(
            check("SyringeOn", semantic.get("SyringeOn", "<missing>"), False, semantic.get("SyringeOn") is False)
        )
        checks.append(
            check("FilterHeadOn", semantic.get("FilterHeadOn", "<missing>"), False,
                  semantic.get("FilterHeadOn") is False)
        )
        mounted_syringe = sorted(
            str(name) for name, value in attached.items() if value is True and is_syringe(name)
        )
        checks.append(check("syringe components released", mounted_syringe, [], not mounted_syringe))

    def allowed_filter_mount(name: str, path: Any) -> bool:
        return (
            syringe_used
            and syringe_released
            and name in installed_filters
            and isinstance(path, str)
            and path.endswith(f"/FilterMount_{name}")
        )

    disallowed_attached = sorted(
        str(name)
        for name, value in attached.items()
        if value is True and not (name in installed_filters and syringe_released)
    )
    checks.append(check("attached tools released", disallowed_attached, [], not disallowed_attached))
    held_tools = sorted(
        str(name)
        for name, path in grips.items()
        if not allowed_filter_mount(str(name), path)
    )
    checks.append(check("held tools released", held_tools, [], not held_tools))
    return checks


class MatterixWorkstation(SilaDevice, type="matterix_workstation"):
    """
    Matterix (Isaac Sim) digital-twin workstation behind the ChemOS 2.0 Matterix SiLA 2 bridge.

    Opens one simulation session for the device's lifetime and runs registered capabilities in it.
    """

    @classmethod
    def editor_presets(cls, init_parameters: dict[str, Any]) -> dict[str, Any]:
        from matterix_sim.editor import workstation_scene  # noqa: PLC0415

        return workstation_scene(init_parameters["scene_id"])

    @classmethod
    def check_plan(cls, init_parameters: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, str]]:
        from integrations.matterix.bridge.plan_check import check_plan  # noqa: PLC0415

        return check_plan(init_parameters["scene_id"], steps)

    class Config(SilaDevice.Config):
        scene_id: str  # e.g. franka_beaker or pipetting_realtime
        record_video: bool = False
        hide_shell: bool = False
        experiment_id: str = "eos"
        resource_map: dict[str, str] = Field(default_factory=dict)  # EOS resource name -> scene object
        consumable_map: dict[str, list[str]] = Field(default_factory=dict)  # Stock resource -> disposable scene objects
        ready_timeout_s: float = Field(180, gt=0)

    async def _initialize(self, config: Config) -> None:
        self._session_id = None
        self._experiment_owner = None
        self._blocked = False
        self._requests = {}
        self._results = {}
        self._recording_settings = {}
        self._boundary_acceptances = {}
        self._stopped = set()
        self._resource_map = config.resource_map
        self._consumable_map = config.consumable_map
        self._ready_timeout_s = config.ready_timeout_s
        self._scene_id = config.scene_id
        self._experiment_id = config.experiment_id
        self._default_recording = {
            "record_video": config.record_video,
            "hide_shell": config.hide_shell,
        }
        self._camera_enabled = config.record_video or config.scene_id == "franka_beaker"
        self._generation = 0
        await super()._initialize(config)
        # The bridge never reopens an ended session, so every initialization starts a new one
        session_id = f"eos-{self.name}-{uuid.uuid4().hex[:12]}"[:64]
        await self._call(
            "CreateSession",
            {
                "session_id": session_id,
                "experiment_id": config.experiment_id,
                "scene_id": config.scene_id,
                "enable_cameras": self._camera_enabled,
                "record_video": config.record_video,
            },
        )
        self._session_id = session_id
        try:
            await self._wait_ready()
        except Exception:
            try:
                await self._cleanup()
            except Exception:
                log.error(f"Failed to close Matterix session '{session_id}' after initialization failed", exc_info=True)
            raise

    async def _cleanup(self) -> None:
        if self._session_id is None:  # Initialization failed before a session existed
            return
        current = await self._call("GetSession", self._session_id)
        if current.get("active_task_id"):
            await self._stop_request(current["active_task_id"])
        state = await self._call("EndSession", self._session_id)
        # Isaac Sim shuts down after EndSession returns; wait so the workstation is free for the next session
        deadline = time.monotonic() + SESSION_END_TIMEOUT
        while state["status"] not in ("ended", "interrupted"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Matterix session '{self._session_id}' did not end in {SESSION_END_TIMEOUT} s")
            await asyncio.sleep(1)
            state = await self._call("GetSession", self._session_id)

    async def _report(self) -> dict[str, Any]:
        return {
            **await super()._report(),
            "session": await self._call("GetSession", self._session_id),
            "experiment_owner": self._experiment_owner,
            "blocked": self._blocked,
        }

    async def _wait_ready(self, minimum_generation: int = 0) -> dict:
        deadline = time.monotonic() + self._ready_timeout_s
        while time.monotonic() < deadline:
            state = await self._call("GetSession", self._session_id)
            if state["status"] == "idle" and state.get("generation", 0) >= minimum_generation:
                self._generation = state.get("generation", 0)
                return state
            if state["status"] in {"frozen", "interrupted", "ended"}:
                raise RuntimeError(f"Matterix session is {state['status']}: {state.get('error')}")
            await asyncio.sleep(0.25)
        raise TimeoutError("Matterix session did not become ready")

    async def _ensure_camera_session(self, record_video: bool) -> None:
        """Move an idle station session to a camera-enabled worker before recording starts."""
        if not record_video or self._scene_id != "pipetting_realtime" or getattr(self, "_camera_enabled", False):
            return
        if self._blocked or self._experiment_owner is not None:
            raise RuntimeError("Cannot replace a session owned by an active or uncertain experiment")
        previous = await self._call("GetSession", self._session_id)
        if previous.get("status") == "starting":
            previous = await self._wait_ready()
        if previous.get("active_task_id") or previous.get("status") != "idle":
            raise RuntimeError("Cannot replace an active or uncertain Matterix session")

        await self._call("EndSession", self._session_id)
        deadline = time.monotonic() + SESSION_END_TIMEOUT
        while previous.get("status") not in ("ended", "interrupted"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Matterix session '{self._session_id}' did not end before camera setup")
            await asyncio.sleep(1)
            previous = await self._call("GetSession", self._session_id)
        if previous["status"] != "ended":
            raise RuntimeError(f"Matterix session ended as {previous['status']} before camera setup")

        session_id = f"eos-{self.name}-{uuid.uuid4().hex[:12]}"[:64]
        getattr(self, "_requests", {}).clear()
        getattr(self, "_results", {}).clear()
        getattr(self, "_recording_settings", {}).clear()
        getattr(self, "_boundary_acceptances", {}).clear()
        getattr(self, "_stopped", set()).clear()
        self._generation = 0
        await self._call(
            "CreateSession",
            {
                "session_id": session_id,
                "experiment_id": self._experiment_id,
                "scene_id": self._scene_id,
                "enable_cameras": True,
                "record_video": False,
            },
        )
        self._session_id = session_id
        self._camera_enabled = True

    async def begin_experiment(
        self,
        protocol_run_name: str,
        task_name: str,
        record_video: bool | None = None,
        hide_shell: bool | None = None,
    ) -> dict:
        if not protocol_run_name:
            raise ValueError("An experiment requires an EOS protocol run identity")
        key = (protocol_run_name, task_name)
        if self._blocked or self._experiment_owner is not None or key in self._stopped:
            raise RuntimeError("Workstation requires recovery or already belongs to an experiment")
        requested = {"record_video": record_video, "hide_shell": hide_shell}
        settings = recording_settings(
            {name: value for name, value in requested.items() if isinstance(value, bool)},
            defaults=getattr(self, "_default_recording", {}),
        )
        await self._ensure_camera_session(settings["record_video"])
        self._experiment_owner = protocol_run_name
        settings_by_key = getattr(self, "_recording_settings", {})
        settings_by_key[key] = settings
        self._recording_settings = settings_by_key
        await self._wait_ready()
        if key in self._stopped:
            raise RuntimeError("Experiment was stopped before scene reset")
        generation = self._generation
        await self._call("ResetSession", self._session_id)
        state = await self._wait_ready(generation + 1)
        if key in self._stopped:
            raise RuntimeError("Experiment was stopped during scene reset")
        if not state["runtime"]["reset_acceptance"]["passed"]:
            raise RuntimeError("Initial-state acceptance failed")
        self._boundary_acceptances = getattr(self, "_boundary_acceptances", {})
        self._boundary_acceptances[key] = state["runtime"]["reset_acceptance"]
        return state

    async def end_experiment(self, protocol_run_name: str, task_name: str, expected_phase: int) -> dict:
        if protocol_run_name is None or self._experiment_owner != protocol_run_name or self._blocked:
            raise RuntimeError("Experiment does not own a usable workstation")
        state = await self._wait_ready()
        checks = [{"name": "实验所有权", "actual": self._experiment_owner,
                   "expected": protocol_run_name, "passed": True}]
        if expected_phase == 0:
            requests = {key: request for key, request in self._requests.items() if key[0] == protocol_run_name}
            if not requests:
                raise RuntimeError("Atomic experiment has no executed capabilities")
            states = [(key, self._results.get(key)) for key in requests]
            if any(
                result is None
                or result.get("status") != "succeeded"
                or (result.get("result") or {}).get("acceptance", {}).get("passed") is not True
                for _, result in states
            ):
                raise RuntimeError("Atomic experiment has a failed, nonterminal, or unaccepted capability")
            if state.get("active_task_id"):
                raise RuntimeError("Atomic experiment still has an active task")
            status = (
                state.get("runtime", {})
                .get("latest_sample", {})
                .get("semantics", {})
                .get("device_state", {})
                .get("operation_status")
            )
            if not _operation_accepted(status):
                raise RuntimeError(f"Atomic experiment ended with operation status {status!r}")
            checks.extend(
                {"name": f"{key[1]} 操作验收", "actual": result["result"]["acceptance"]["passed"],
                 "expected": True, "passed": True}
                for key, result in states
            )
            checks.append({"name": "最终操作状态", "actual": status,
                           "expected": "accepted 或 idle", "passed": True})
        else:
            phase = state["runtime"]["latest_sample"]["semantics"]["experiment_phase"]
            if phase != expected_phase:
                raise RuntimeError(f"Experiment ended after phase {phase}; expected {expected_phase}")
            checks.append({"name": "完成阶段", "actual": phase, "expected": expected_phase, "passed": True})
        if self._scene_id == "pipetting_realtime":
            semantics = state.get("runtime", {}).get("latest_sample", {}).get("semantics") or {}
            closure_checks = _station_closure_checks(semantics)
            failed = [item for item in closure_checks if not item["passed"]]
            if failed:
                detail = "；".join(
                    f"{item['name']} actual {item['actual']}, expected {item['expected']}"
                    for item in failed
                )
                raise RuntimeError(f"Station closure gate failed: {detail}")
            checks.extend(closure_checks)
        checks.append({"name": "活动任务已清空", "actual": state.get("active_task_id"),
                       "expected": None, "passed": not state.get("active_task_id")})
        state["runtime"]["experiment_acceptance"] = {"passed": all(c["passed"] for c in checks), "checks": checks}
        self._boundary_acceptances = getattr(self, "_boundary_acceptances", {})
        self._boundary_acceptances[(protocol_run_name, task_name)] = state["runtime"]["experiment_acceptance"]
        self._experiment_owner = None
        return state

    async def recover(self) -> dict:
        """Management action: reset a stopped/frozen scene, then reactivate this device in EOS."""
        state = await self._call("GetSession", self._session_id)
        if state.get("active_task_id"):
            await self._stop_request(state["active_task_id"])
        generation = state.get("generation", 0)
        await self._call("ResetSession", self._session_id)
        state = await self._wait_ready(generation + 1)
        if not state["runtime"]["reset_acceptance"]["passed"]:
            raise RuntimeError("Recovery initial-state acceptance failed")
        self._experiment_owner = None
        self._blocked = False
        return state

    async def run_capability(
        self,
        capability_id: str,
        parameters: dict[str, Any],
        protocol_run_name: str | None = None,
        task_name: str | None = None,
    ) -> dict[str, Any]:
        """Run a registered capability in the session and return its final task state, including the result."""
        key = (protocol_run_name, task_name or uuid.uuid4().hex)
        if self._blocked or key in self._stopped:
            raise RuntimeError("Workstation is frozen; recover before starting another experiment")
        if self._scene_id == "pipetting_realtime" and (
            protocol_run_name is None or self._experiment_owner != protocol_run_name
        ):
            raise RuntimeError("Begin the owning experiment before executing station operations")
        settings = recording_settings(
            parameters=parameters,
            defaults=getattr(self, "_default_recording", {}),
        )
        parameters = {**parameters, **settings}
        if self._scene_id != "pipetting_realtime":
            parameters.pop("hide_shell", None)
        identity = json.dumps([self._session_id, self._generation, *key])
        request = {
            "request_id": "eos-" + hashlib.sha256(identity.encode()).hexdigest()[:56],
            "session_id": self._session_id,
            "capability_id": capability_id,
            "parameters": parameters,
        }
        previous = self._requests.setdefault(key, request)
        if previous != request:
            raise ValueError("This EOS task identity already belongs to different parameters")
        settings_by_key = getattr(self, "_recording_settings", {})
        settings_by_key[key] = settings
        self._recording_settings = settings_by_key
        async with SilaClientContext.connect(self, _client_class(), lock_timeout=None) as client:
            if key in self._stopped:
                raise RuntimeError("Task was stopped before submission")
            instance = client.Matterix.SubmitTask(json.dumps(request))
            while not instance.done:  # noqa: ASYNC110 (sila2 updates `done` from its own gRPC thread)
                await asyncio.sleep(0.5)
            state = json.loads(instance.get_responses().Result)
            self._results[key] = state
            return _checked(state)

    async def get_execution_progress(self, protocol_run_name: str, task_name: str) -> dict[str, Any] | None:
        """Read one registered request through a separate, unlocked SiLA client."""
        request = self._requests.get((protocol_run_name, task_name))
        if request is None:
            return None
        client = await SilaClientContext.create_client(self, _client_class())
        try:
            response = await asyncio.to_thread(client.Matterix.GetRunState, request["request_id"])
        finally:
            client.close()
        return _checked(json.loads(response[0]))

    async def _stop_request(self, request_id: str) -> dict:
        state = await self._call("GetRunState", request_id)
        if state["status"] not in TERMINAL:
            await self._call("CancelRun", request_id)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = await self._call("GetRunState", request_id)
            session = await self._call("GetSession", self._session_id)
            if state["status"] in TERMINAL and not session.get("active_task_id"):
                return state
            await asyncio.sleep(0.1)
        raise TimeoutError("Matterix stop was not confirmed; workstation remains unavailable")

    async def finalize_task(self, protocol_run_name: str | None, task_name: str, stop: bool = False) -> dict:
        key = (protocol_run_name, task_name)
        if stop:
            self._stopped.add(key)
            self._blocked = True
        request = self._requests.get(key)
        state = self._results.get(key)
        files = {}
        evidence_warnings = []
        if request is not None:
            if state is None or state["status"] not in (TERMINAL | {"rejected"}):
                state = (
                    await self._stop_request(request["request_id"])
                    if stop
                    else await self._call("GetRunState", request["request_id"])
                )
            if state["status"] in TERMINAL:
                async with SilaClientContext.connect(self, _client_class(), lock_timeout=None) as client:
                    response = await asyncio.to_thread(client.Matterix.GetRunArchive, request["request_id"])
                archive_data = bytes(response[0])
                files["matterix-evidence.zip"] = archive_data
                extracted, evidence_warnings = extract_archive_evidence(archive_data)
                files.update(extracted)
            if state["status"] != "succeeded":
                self._blocked = True
        session = await self._call("GetSession", self._session_id)
        if stop and request is None and session["status"] in {"starting", "resetting"}:
            session = await self._wait_ready()
        if session["status"] in {"frozen", "interrupted", "ended"}:
            self._blocked = True
        sample = session.get("runtime", {}).get("latest_sample") or {}
        semantics = sample.get("semantics") or {}
        result = (state or {}).get("result") or {}
        settings = getattr(self, "_recording_settings", {}).get(key) or recording_settings(
            parameters=(request or {}).get("parameters"),
            defaults=getattr(self, "_default_recording", {}),
        )
        parameters = {
            "protocol_run_name": protocol_run_name,
            "task_name": task_name,
            "session_id": self._session_id,
            "generation": session.get("generation"),
            "request_id": request["request_id"] if request else None,
            "status": (state or {}).get("status", session["status"]),
            "acceptance": result.get("acceptance") or getattr(self, "_boundary_acceptances", {}).get(key),
            "execution_mode": session.get("runtime", {}).get("execution_mode", "physics"),
            "hardware_calibrated": False,
            **settings,
            "evidence_warnings": evidence_warnings,
            "final_semantics": semantics,
        }
        evidence = {"eos": parameters, "request": request, "state": state, "session": session}
        files["matterix-context.json"] = json.dumps(evidence, ensure_ascii=False, allow_nan=False).encode()
        device = semantics.get("device_state") or {}
        volumes = semantics.get("liquid_volumes_ml") or {}
        used = {e.get("object") for e in device.get("operation_events", []) if e.get("event") == "grasp"}
        resources = {
            name: {
                "asset_id": asset,
                "source": "simulation",
                "volume_ml": volumes.get(asset),
                "used": asset in used,
                "held": asset in device.get("grips", {}),
                "cap_closed": device.get("cap_closed", {}).get(asset),
                "session_id": self._session_id,
                "generation": session.get("generation"),
                "needs_recovery": self._blocked,
            }
            for name, asset in self._resource_map.items()
        }
        return {
            "parameters": parameters,
            "files": files,
            "resource_updates": resources,
            "inventory_events": consumable_events(
                semantics, getattr(self, "_consumable_map", {}), self._session_id, session.get("generation", 0)
            ),
            "available": not self._blocked,
        }

    async def _call(self, command: str, argument: Any) -> dict[str, Any]:
        """Call an unobservable bridge command; its JSON argument is a request object or a session id."""
        payload = argument if isinstance(argument, str) else json.dumps(argument)
        async with SilaClientContext.connect(self, _client_class(), lock_timeout=None) as client:
            response = await asyncio.to_thread(getattr(client.Matterix, command), payload)
        return _checked(json.loads(response[0]))


def _client_class() -> type:
    from sila2.client import SilaClient  # noqa: PLC0415 (sila2 is an optional dependency)

    return SilaClient


def _checked(state: dict[str, Any]) -> dict[str, Any]:
    """The bridge reports every rejection, such as a busy workstation, as {"status": "rejected"}."""
    if state.get("status") == "rejected":
        raise RuntimeError(f"Matterix rejected the request: {state.get('error')}")
    return state


def _operation_accepted(status: Any) -> bool:
    if status in (None, "", "idle"):
        return True
    if not isinstance(status, str):
        return False
    return all(part == "accepted" or part.partition(": ")[2] == "accepted" for part in status.split(" | "))
