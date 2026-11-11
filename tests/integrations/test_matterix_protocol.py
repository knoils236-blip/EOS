from pathlib import Path
from unittest.mock import Mock

import pytest

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.protocol_graph import ProtocolGraph
from eos.orchestration.services.lab_service import LabService
from eos.scheduling.utils import compute_hold_users


def test_full_station_holds_one_device_until_finish():
    root = Path(__file__).resolve().parents[2]
    config = ConfigurationManager(str(root / "user"), {"matterix_sim"})
    config.load_lab("matterix_pipetting_lab")
    config.load_protocol("matterix_full_station")
    protocol = config.protocols["matterix_full_station"]
    assert len(protocol.tasks) == 9
    assert len({t.name for t in protocol.tasks}) == 9
    graph = ProtocolGraph(protocol)
    users, _ = compute_hold_users(graph)
    assert users[("begin", "matterix")] == frozenset(t.name for t in protocol.tasks[1:])
    assert users[("close_door", "matterix")] == frozenset({"finish"})
    assert all(t.retries == 0 for t in protocol.tasks)
    assert ("finish", "matterix") not in users


@pytest.mark.parametrize(("protocol_type", "task_count"), [
    ("matterix_station_skills", 26), ("matterix_station_operations", 12),
])
def test_station_protocols_cover_the_reference_work_and_keep_one_owner(protocol_type, task_count):
    root = Path(__file__).resolve().parents[2]
    config = ConfigurationManager(str(root / "user"), {"matterix_sim"})
    config.load_lab("matterix_pipetting_lab")
    config.load_protocol(protocol_type)
    protocol = config.protocols[protocol_type]
    assert len(protocol.tasks) == task_count
    assert not LabService(config, Mock()).check_device_plans(protocol)
    users, _ = compute_hold_users(ProtocolGraph(protocol))
    assert users[("begin", "matterix")] == frozenset(t.name for t in protocol.tasks[1:])
    assert ("finish", "matterix") not in users
    assert protocol.tasks[-1].parameters["expected_phase"] == 0
    assert all(t.retries == 0 for t in protocol.tasks)
