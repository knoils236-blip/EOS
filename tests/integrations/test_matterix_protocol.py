from pathlib import Path

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.protocol_graph import ProtocolGraph
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
