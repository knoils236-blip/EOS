"""F08: preflight evidence states its levels honestly and cannot be forged by a client."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eos.configuration.entities.lab_def import LabDef
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.protocols.exceptions import EosProtocolRunExecutionError
from eos.protocols.preflight import preflight_evidence, reject_reserved_meta


def test_a_twin_without_declared_fidelity_is_never_read_as_physics():  # A1
    labs = {"mock_twin": SimpleNamespace(fidelity=None), "physics_twin": SimpleNamespace(fidelity="L3")}
    evidence = preflight_evidence("run__preflight", "plan", {"a": "mock_twin", "b": "physics_twin"}, labs)
    assert evidence["levels"] == ["L0", "L3", "undeclared"]
    assert "L4" not in evidence["levels"]


@pytest.mark.parametrize("key", ["preflight_run", "preflight_for", "preflight_evidence"])
def test_a_client_cannot_mark_its_own_run_as_preflighted(key):  # A4
    with pytest.raises(EosProtocolRunExecutionError, match="set by EOS"):
        reject_reserved_meta(ProtocolRunSubmission(name="r", type="p", meta={key: "anything"}))
    reject_reserved_meta(ProtocolRunSubmission(name="r", type="p", meta={"preflight": {"real": "twin"}}))


def test_only_simulated_labs_declare_a_fidelity():
    LabDef(name="twin", desc="d", simulated=True, fidelity="L3", devices={})
    with pytest.raises(ValidationError, match="Only simulated labs"):
        LabDef(name="real", desc="d", fidelity="L3", devices={})
