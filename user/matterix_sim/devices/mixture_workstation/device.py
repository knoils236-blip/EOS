"""The same preparation contract for a fast mock and the Matterix workstation."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from eos.devices.base_device import BaseDevice
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation
from matterix_sim.mixture import MAX_UL, MIN_UL, TOTAL_UL, Sample, sample_identity
from matterix_sim.observations import recording_settings


class MixtureWorkstation(BaseDevice, type="mixture_workstation"):
    @classmethod
    def editor_presets(cls, init_parameters: dict[str, Any]) -> dict[str, Any]:
        from matterix_sim.editor import mixture_scene  # noqa: PLC0415

        return mixture_scene(cls.Config.model_validate(init_parameters).backend)

    class Config(BaseDevice.Config):
        backend: Literal["mock", "matterix"] = "mock"
        address: str = "127.0.0.1"
        port: int = Field(65200, ge=1, le=65535)
        journal_dir: Path = Path(".state/mixture")

    async def _initialize(self, config: Config) -> None:
        self._backend = config.backend
        self._driver = None
        self._runs = {}
        self._blocked = False
        self._journal_lock = asyncio.Lock()
        identity = [self.lab_name, self.name, config.backend, config.address, config.port]
        namespace = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
        self._journal_dir = config.journal_dir / namespace
        await asyncio.to_thread(self._journal_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._restore_journal)
        if config.backend == "matterix":
            self._driver = MatterixWorkstation(self.name + "-executor", self.lab_name)
            await self._driver.initialize(
                {"address": config.address, "port": config.port, "insecure": True, "scene_id": "pipetting_realtime"}
            )

    async def _cleanup(self) -> None:
        if self._driver is not None:
            await self._driver.cleanup()

    async def _report(self) -> dict:
        return {
            "backend": self._backend,
            "blocked": self._blocked,
            "execution": await self._driver.report() if self._driver else None,
        }

    async def recover(self) -> dict:
        state = await self._driver.recover() if self._driver else {"status": "idle"}
        for key, run in self._runs.items():
            if run.get("active") or run.get("needs_recovery"):
                run["reconciled"] = True
                run["needs_recovery"] = False
                await self._persist(key)
        self._blocked = False
        return state

    def _restore_journal(self) -> None:
        for path in self._journal_dir.glob("*.json"):
            record = json.loads(path.read_text())
            run = record["run"]
            run["reports"] = []
            self._runs[tuple(record["key"])] = run
            if (run.get("active") and not run.get("reconciled")) or run.get("needs_recovery"):
                self._blocked = True

    async def _persist(self, key: tuple) -> None:
        async with self._journal_lock:
            run = {name: value for name, value in self._runs[key].items() if name != "reports"}
            identifier = hashlib.sha256(json.dumps(key).encode()).hexdigest()
            path = self._journal_dir / (identifier + ".json")
            data = json.dumps({"key": key, "run": run}, allow_nan=False)

            def write() -> None:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(data)
                temporary.replace(path)

            await asyncio.to_thread(write)

    async def prepare(
        self,
        protocol_run_name: str,
        task_name: str,
        stock_volume_ul: float,
        settings: dict[str, Any] | None = None,
    ) -> dict:
        if not MIN_UL <= stock_volume_ul <= MAX_UL:
            raise ValueError("stock_volume_ul must be finite and between 20 and 180 uL")
        if not protocol_run_name:
            raise ValueError("Preparation requires a protocol run identity")
        key = (protocol_run_name, task_name)
        if self._blocked:
            raise RuntimeError("Workstation requires explicit recovery")
        recording = recording_settings(meta=settings)
        if key in self._runs:
            existing = self._runs[key]
            if existing["volume_ul"] != stock_volume_ul or existing.get("settings", recording) != recording:
                raise ValueError("Task identity already belongs to different preparation parameters")
            if existing.get("sample"):
                return existing["sample"]
            raise RuntimeError("Preparation outcome unresolved; reconcile before starting a new run")
        run = {
            "volume_ul": stock_volume_ul,
            "settings": recording,
            "active": "begin",
            "reports": [],
            "operations": [],
        }
        self._runs[key] = run
        await self._persist(key)
        stock, diluent = stock_volume_ul, TOTAL_UL - stock_volume_ul
        if self._driver:
            try:
                stock, diluent = await self._prepare_matterix(key, stock, diluent)
            except Exception:
                self._blocked = True
                raise
        else:
            run["operations"] = [
                {"step": "stock", "source": "TransferBottle10ml_617", "volume_ul": stock},
                {"step": "diluent", "source": "DesignD_DesignC_BottleBody500ml_238", "volume_ul": diluent},
            ]
        sample = Sample(
            sample_id=sample_identity(protocol_run_name + ":" + task_name),
            protocol_run_name=protocol_run_name,
            backend="simulation" if self._driver else "mock",
            requested_stock_ul=stock_volume_ul,
            stock_ul=stock,
            diluent_ul=diluent,
            operations=run["operations"],
            composition_source="isaac_semantic_ledger" if self._driver else "mock_material_balance",
        )
        run["active"] = None
        run["sample"] = sample.model_dump(mode="json")
        await self._persist(key)
        return run["sample"]

    async def _prepare_matterix(self, key: tuple, stock: float, diluent: float) -> tuple[float, float]:
        protocol_run_name, task_name = key
        run = self._runs[key]
        await self._driver.begin_experiment(
            protocol_run_name,
            task_name + "-begin",
            run["settings"]["record_video"],
            run["settings"]["hide_shell"],
        )
        await self._save_report(key, "begin")
        steps = [
            ("open_door", "station_full_station_open_door", {}),
            ("prepare_vial", "station_full_station_prepare_vial", {}),
            ("stock", "station_mixture_stock", {"volume_ul": stock}),
            ("open_bottle", "station_full_station_open_bottle", {}),
            ("diluent", "station_mixture_diluent", {"volume_ul": diluent}),
            ("close", "station_mixture_close", {}),
        ]
        for step, capability, parameters in steps:
            if self._blocked:
                raise RuntimeError("Preparation was stopped")
            run["active"] = step
            await self._persist(key)
            effective_parameters = {**parameters, **run["settings"]}
            state = await self._driver.run_capability(
                capability, effective_parameters, protocol_run_name, task_name + "-" + step
            )
            await self._save_report(key, step)
            result = state["result"]
            if state["status"] != "succeeded" or not result["acceptance"]["passed"]:
                raise RuntimeError(f"Execution failed during {step}: {state.get('error')}")
            if step in {"stock", "diluent"}:
                target = "DesignC_BottleBody50ml_231"
                before = result["initial"]["semantics"]["liquid_volumes_ml"][target]
                after = result["final"]["semantics"]["liquid_volumes_ml"][target]
                if step == "stock":
                    stock = (after - before) * 1000
                else:
                    diluent = (after - before) * 1000
            run["operations"].append(
                {
                    "step": step,
                    "request_id": state["request_id"],
                    "capability_id": capability,
                    "parameters": effective_parameters,
                }
            )
        run["active"] = "finish"
        await self._persist(key)
        await self._driver.end_experiment(protocol_run_name, task_name + "-finish", expected_phase=6)
        await self._save_report(key, "finish")
        return stock, diluent

    async def get_execution_progress(self, protocol_run_name: str, task_name: str) -> list[dict]:
        """Return registered stage states for one mixture preparation."""
        key = (protocol_run_name, task_name)
        run = self._runs.get(key)
        if not run or not self._driver:
            return []
        steps = [operation["step"] for operation in run.get("operations", [])]
        active = run.get("active")
        if active and active not in {"begin", "finish", *steps}:
            steps.append(active)
        states = []
        for step in steps:
            state = await self._driver.get_execution_progress(protocol_run_name, task_name + "-" + step)
            if state is not None:
                states.append(state)
        return states

    async def _save_report(self, key: tuple, step: str, stop: bool = False) -> None:
        report = await self._driver.finalize_task(key[0], key[1] + "-" + step, stop)
        self._runs[key]["reports"].append((step, report))

    async def finalize_task(self, protocol_run_name: str | None, task_name: str, stop: bool = False) -> dict:
        key = (protocol_run_name, task_name)
        if stop:
            self._blocked = True
        run = self._runs.get(key)
        if run and self._driver and run["active"]:
            await self._save_report(key, run["active"], stop)
        files, updates = {}, {}
        for step, report in run["reports"] if run else []:
            files.update({step + "-" + name: data for name, data in report["files"].items()})
            updates.update(report["resource_updates"])
        parameters = {
            "backend": self._backend,
            "sample_id": (run or {}).get("sample", {}).get("sample_id"),
            "commands": (run or {}).get("operations", []),
            "record_video": (run or {}).get("settings", {}).get("record_video", False),
            "hide_shell": (run or {}).get("settings", {}).get("hide_shell", False),
            "needs_recovery": self._blocked,
            "failure_kind": "execution_or_infrastructure" if self._blocked else "none",
        }
        if run:
            run["needs_recovery"] = self._blocked
            await self._persist(key)
        files["preparation-context.json"] = json.dumps(parameters, allow_nan=False).encode()
        return {"parameters": parameters, "files": files, "resource_updates": updates, "available": not self._blocked}
