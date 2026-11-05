"""Demo stock usage from observed first grasps, never from submitted task names."""

import hashlib


def consumable_events(semantics: dict, stock_map: dict[str, list[str]], session_id: str, generation: int) -> list[dict]:
    used = {
        event.get("object")
        for event in (semantics.get("device_state") or {}).get("operation_events", [])
        if event.get("event") == "grasp"
    }
    return [
        {
            "resource_name": stock,
            "event_id": "matterix-" + hashlib.sha256(f"{session_id}:{generation}:{asset}".encode()).hexdigest(),
            "delta": -1,
            "source": "simulation",
            "note": f"仿真耗材首次使用：{asset}",
        }
        for stock, assets in sorted(stock_map.items())
        for asset in sorted(set(assets) & used)
    ]
