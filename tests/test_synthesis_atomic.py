from __future__ import annotations

import json

import pytest

from yb_sse_devices import YBSynthesisAtomicStation


@pytest.fixture()
def station() -> YBSynthesisAtomicStation:
    value = YBSynthesisAtomicStation(
        config={
            "simulation": True,
            "simulation_crucible_id": "CRU-SIM-001",
            "simulation_dosing_duration": 0.1,
            "simulation_handshake_delay": 0.01,
            "business_firing_duration": 0.1,
        },
        action_timeout=5,
        simulation_step=0.1,
    )
    try:
        yield value
    finally:
        value.close()


def _record(station: YBSynthesisAtomicStation, item_id: str) -> dict:
    rows = json.loads(station.resource_status()["ledger_json"])
    return next(row for row in rows if row["item_id"] == item_id)


def test_synthesis_atomic_chain_uses_modbus_and_commits_ledger(
    station: YBSynthesisAtomicStation,
) -> None:
    created = station.create_batch(task_slot_nums=[1])
    task_id = created["task_id"]
    crucible_id = "CRU-SIM-001"

    station.load_big_crucible(task_id)
    assert _record(station, crucible_id)["location"] == "synthesis_chamber"

    station.dose_recipe(task_id)
    assert _record(station, crucible_id)["synthesis_state"] == "dosed"
    station.resonate_and_unload(task_id)
    assert _record(station, crucible_id)["location"] == "bottle_station"

    bottled = station.bottle_and_prepare_post(task_id)
    assigned = station.assign_small_crucibles(bottled["post_id"], bottled["lot_id"])
    unloaded = station.sinter_and_unload(assigned["post_id"])
    small_id = unloaded["small_cubics"][0]
    assert _record(station, small_id)["location"] == "stock_buffer"

    station.store_fired_material(small_id, "BOTTLE-ATOMIC-001", bottled["lot_id"])
    assert _record(station, small_id)["location"] == "stock"


def test_synthesis_atomic_actions_reject_interleaved_task(
    station: YBSynthesisAtomicStation,
) -> None:
    created = station.create_batch(task_slot_nums=[1])
    with pytest.raises(RuntimeError, match="不能交错"):
        station.load_big_crucible("TASK-OTHER")
    assert station.task_id == created["task_id"]

