"""Regenerate (and add) the capabilities.json and station_catalog.json entries of station_skills.SKILLS.

Run after editing a skill in bridge/station_skills.py: python tools/sync_station_skills.py
The bridge config version covers both JSON files, so restart the bridge afterwards.
"""

import json
from pathlib import Path
import sys

INTEGRATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION_ROOT))
from bridge import station_skills  # noqa: E402
from bridge.capabilities import CAPABILITIES_PATH, STATION_CATALOG_PATH  # noqa: E402


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    capabilities = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    station = json.loads(STATION_CATALOG_PATH.read_text(encoding="utf-8"))
    by_id = {item["id"]: item["workflow_name"] for item in station_skills.SKILLS}
    present = {item["id"] for item in capabilities}
    capabilities = [
        station_skills.capability_entry(by_id[item["id"]]) if item["id"] in by_id else item for item in capabilities
    ] + [station_skills.capability_entry(by_id[key]) for key in by_id if key not in present]
    for capability_id, workflow_name in by_id.items():
        station["enabled_capabilities"][capability_id] = station_skills.catalog_enabled_entry(workflow_name)
        station["workflows"][workflow_name] = station_skills.catalog_workflow_entry(workflow_name)
    _write(CAPABILITIES_PATH, capabilities)
    _write(STATION_CATALOG_PATH, station)


if __name__ == "__main__":
    main()
