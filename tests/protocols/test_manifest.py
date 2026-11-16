"""F04: immutable, content-addressed run plans bound to resume, preflight and approval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import eos.orchestration.services.protocol_service as protocol_service_module
from eos.orchestration.services.protocol_service import ProtocolService
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.protocols.exceptions import EosProtocolRunExecutionError
from eos.protocols.manifest import REDACTED, differences, manifest_for, redact
from eos.protocols.protocol_executor import ProtocolExecutor
from tests.fixtures import *

LAB, PROTOCOL = "small_lab", "water_purification"
PARAMETERS = {
    "mixing": {"speed": 50, "time": 120},
    "evaporation": {"evaporation_temperature": 120, "evaporation_rotation_speed": 200, "evaporation_sparging_flow": 5},
}


def submission(name="run-1", owner="a", parameters=PARAMETERS, resume=False) -> ProtocolRunSubmission:
    return ProtocolRunSubmission(name=name, type=PROTOCOL, owner=owner, parameters=parameters, resume=resume)


def test_secrets_and_url_credentials_never_enter_a_manifest():
    content = redact(
        {
            "password": "hunter2",
            "endpoint": "https://robot:s3cret@lab.example/api",
            "devices": [{"api_key": "k", "speed": 5}],
        }
    )
    assert content == {
        "password": REDACTED,
        "endpoint": f"https://{REDACTED}@lab.example/api",
        "devices": [{"api_key": REDACTED, "speed": 5}],
    }


@pytest.mark.parametrize("setup_lab_protocol", [(LAB, PROTOCOL)], indirect=True)
class TestRunManifest:
    def test_a_plan_has_one_hash_and_every_execution_change_alters_it(
        self, setup_lab_protocol, configuration_manager, monkeypatch
    ):
        base = manifest_for(configuration_manager, submission())
        # Who submits it and under which name does not change what runs
        assert manifest_for(configuration_manager, submission(name="run-2", owner="b")).hash == base.hash

        changed = manifest_for(
            configuration_manager, submission(parameters={**PARAMETERS, "mixing": {"speed": 60, "time": 120}})
        )
        assert differences(base.content, changed.content) == ["parameters.mixing"]

        monkeypatch.setitem(configuration_manager.labs[LAB].devices["magnetic_mixer"].init_parameters, "max_speed", 40)
        monkeypatch.setitem(configuration_manager.tasks.fingerprints, "Magnetic Mixing", "edited-and-reloaded")
        edited = manifest_for(configuration_manager, submission())
        assert differences(base.content, edited.content) == [f"labs.{LAB}", "task_plugins.Magnetic Mixing"]

    def _executor(self, run, manifest, fixtures) -> ProtocolExecutor:
        protocol_run_manager, task_manager, task_executor, scheduler, protocol_graph, db_interface = fixtures
        return ProtocolExecutor(
            protocol_run_submission=run,
            protocol_graph=protocol_graph,
            protocol_run_manager=protocol_run_manager,
            task_manager=task_manager,
            task_executor=task_executor,
            scheduler=scheduler,
            db_interface=db_interface,
            manifest=manifest,
        )

    @pytest.fixture
    def fixtures(
        self, protocol_run_manager, task_manager, task_executor, greedy_scheduler, protocol_graph, db_interface
    ):
        return protocol_run_manager, task_manager, task_executor, greedy_scheduler, protocol_graph, db_interface

    async def test_resume_continues_only_the_plan_the_run_started_with(
        self, configuration_manager, fixtures, db_interface, protocol_run_manager, monkeypatch
    ):
        planned = manifest_for(configuration_manager, submission())
        async with db_interface.get_async_session() as db:
            first = self._executor(submission(), planned, fixtures)
            await first.start_protocol_run(db)
            await first.abandon(db)
            assert (await protocol_run_manager.get_protocol_run(db, "run-1")).manifest_hash == planned.hash

            monkeypatch.setitem(configuration_manager.tasks.fingerprints, "Magnetic Mixing", "edited-and-reloaded")
            changed = manifest_for(configuration_manager, submission(resume=True))
            with pytest.raises(EosProtocolRunExecutionError, match=r"plan changed .*task_plugins\.Magnetic Mixing"):
                await self._executor(submission(resume=True), changed, fixtures).start_protocol_run(db)

            # With the original plan the run resumes
            again = self._executor(submission(resume=True), planned, fixtures)
            await again.start_protocol_run(db)
            await again.abandon(db)

    async def test_a_run_from_before_manifests_stays_unknown_until_resumed(
        self, configuration_manager, fixtures, db_interface, protocol_run_manager
    ):
        async with db_interface.get_async_session() as db:
            legacy = self._executor(submission(), None, fixtures)
            await legacy.start_protocol_run(db)
            await legacy.abandon(db)
            assert (await protocol_run_manager.get_protocol_run(db, "run-1")).manifest_hash is None

            planned = manifest_for(configuration_manager, submission(resume=True))
            resumed = self._executor(submission(resume=True), planned, fixtures)
            await resumed.start_protocol_run(db)
            await resumed.abandon(db)
            assert (await protocol_run_manager.get_protocol_run(db, "run-1")).manifest_hash == planned.hash


@pytest.mark.parametrize("hash_at_release", ["planned", "changed"])
async def test_a_preflight_releases_only_the_plan_it_checked(monkeypatch, hash_at_release):
    service = object.__new__(ProtocolService)
    real = ProtocolRunSubmission(name="real", type="assay")
    service._configuration_manager = SimpleNamespace(labs={"twin": SimpleNamespace(fidelity="L3")})
    service._awaiting_preflight = {"real__preflight": real}
    service._preflighted_plans = {"real": ("planned", {"real_lab": "twin"})}
    service._db_interface = SimpleNamespace(get_async_session=_NoSession)
    service.submit_protocol_run = AsyncMock()
    monkeypatch.setattr(protocol_service_module, "manifest_for", lambda *_: SimpleNamespace(hash=hash_at_release))

    await service._release_preflighted_runs(completed=["real__preflight"], failed=[])

    assert service.submit_protocol_run.await_count == (1 if hash_at_release == "planned" else 0)
    assert service._preflighted_plans == {}
    if hash_at_release == "planned":  # F08: the started run carries what its preflight showed, bound to the plan
        evidence = service.submit_protocol_run.await_args.args[1].meta["preflight_evidence"]
        assert (evidence["plan"], evidence["levels"], evidence["run"]) == ("planned", ["L0", "L3"], "real__preflight")


class _NoSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False
