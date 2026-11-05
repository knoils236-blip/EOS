"""Parameterised two-source mixture; the fixed seven-phase regression is unchanged."""
# ruff: noqa: PLC0415 -- station_semantics dispatches into this module

WORKFLOWS = {"mixture_stock": 3, "mixture_diluent": 5, "mixture_close": 6}
MIN_UL, MAX_UL, TOTAL_UL = 20.0, 180.0, 200.0
STOCK_PHASE = 3
DOOR_TOLERANCE_M, DOOR_OPEN_MIN_M = 0.001, 0.38


def preconditions(workflow, snapshot, parameters=None) -> list[dict]:
    from . import station_semantics as s

    phase = WORKFLOWS[workflow]
    checks = [
        s._check(
            "Mixture phase", snapshot["experiment_phase"], phase - 1, "phase", snapshot["experiment_phase"] == phase - 1
        )
    ]
    if workflow != "mixture_close":
        from .station_experiment import TIP2

        tip = s.FIXED_TIP if phase == STOCK_PHASE else TIP2
        unused = not s._event_observed(s._events(snapshot), "grasp", tip)
        checks.append(s._check("Fresh mixture TIP", unused, True, "bool", unused))
        source = s.SOURCE_BOTTLE if phase == STOCK_PHASE else s.BIG_BOTTLE
        volume = snapshot["liquid_volumes_ml"][source]
        requested_ml = float((parameters or {}).get("volume_ul", MAX_UL)) / 1000.0
        checks.append(
            s._check("Mixture source reserve", volume, f">= {requested_ml:g}", "mL", volume >= requested_ml)
        )
        if phase == STOCK_PHASE:
            empty = abs(snapshot["liquid_volumes_ml"][s.TARGET_BOTTLE]) <= s.VOLUME_TOLERANCE_ML
            checks.append(s._check("Empty target", empty, True, "bool", empty))
        else:
            opened = s._semantic_state(snapshot).get("BigCapOpen") is True
            checks.append(s._check("Diluent bottle open", opened, True, "bool", opened))
    return checks


def evaluate(workflow, initial, final, parameters, history, extrema) -> dict:
    from . import station_semantics as s
    from .station_experiment import TIP2, _liquid_checks

    checks = preconditions(workflow, initial, parameters)
    snapshots = [initial, *history, final]
    if workflow == "mixture_close":
        before = s._device(initial)["joint_positions"]["front_door"]
        after = s._device(final)["joint_positions"]["front_door"]
        checks.append(
            s._check("Mixture door closed", after, 0.0, "m", abs(after) < DOOR_TOLERANCE_M and before > DOOR_OPEN_MIN_M)
        )
    else:
        stock = workflow == "mixture_stock"
        source, tip = (s.SOURCE_BOTTLE, s.FIXED_TIP) if stock else (s.BIG_BOTTLE, TIP2)
        volume = parameters["volume_ul"] / 1000
        checks.extend(s._common_history_checks(initial, snapshots))
        checks.extend(_liquid_checks(initial, final, extrema, source, s.TARGET_BOTTLE, tip, volume))
        events = s._task_events(initial, final)
        for action in ("grasp", "release"):
            found = s._event_observed(events, action, tip)
            checks.append(s._check("Mixture TIP " + action, found, True, "bool", found))
        if not stock:
            total = final["liquid_volumes_ml"][s.TARGET_BOTTLE]
            checks.append(
                s._check(
                    "Mixture total", total, TOTAL_UL / 1000, "mL",
                    abs(total - TOTAL_UL / 1000) <= 2 * s.TRANSFER_TOLERANCE_ML,  # stock + diluent transfers
                )
            )
            closed = s._device(final)["cap_closed"][s.BIG_BOTTLE]
            checks.append(s._check("Diluent bottle recapped", closed, True, "bool", closed is True))
    if workflow != "mixture_stock":
        grips = s._device(final)["grips"]
        checks.append(s._check("Mixture tools released", grips, {}, "state", not grips))
    rejected, details = s._rejection_count(snapshots)
    checks.append(s._check("No rejected operations", details, [], "events", rejected == 0))
    for name, limit in (("air_ml", s.AIR_LIMIT_ML), ("spilled_ml", s.VOLUME_TOLERANCE_ML)):
        delta = final["liquid_balance"][name] - initial["liquid_balance"][name]
        checks.append(s._check(name, delta, 0.0, "mL", abs(delta) <= limit))
    return {"workflow_name": workflow, "passed": all(c["passed"] for c in checks), "checks": checks}
