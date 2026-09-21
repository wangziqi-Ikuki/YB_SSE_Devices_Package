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


def test_runtime_graph_kwargs_reach_nested_modbus_station() -> None:
    station = YBSynthesisAtomicStation(
        simulation=False,
        ip="127.0.0.1",
        port=5020,
        business_simulation=True,
    )
    try:
        assert station.station.simulation is False
        assert station.station.ip == "127.0.0.1"
        assert station.station.port == 5020
        assert station.station.business_state is not None
    finally:
        station.close()


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

    # A long-lived device instance must accept the next batch after the
    # previous batch has reached its terminal physical step.
    next_created = station.create_batch(task_slot_nums=[1])
    assert next_created["task_id"] != task_id


def test_synthesis_atomic_actions_reject_interleaved_task(
    station: YBSynthesisAtomicStation,
) -> None:
    created = station.create_batch(task_slot_nums=[1])
    with pytest.raises(RuntimeError, match="不能交错"):
        station.load_big_crucible("TASK-OTHER")
    assert station.task_id == created["task_id"]


def test_split_atomic_workflow_boundaries_preserve_protocol_and_ledger(
    station: YBSynthesisAtomicStation,
) -> None:
    created = station.create_batch(task_slot_nums=[1], bead_count=80)
    loaded = station.load_big_crucible(created["task_id"])
    confirmed = station.confirm_recipe(loaded["task_id"])
    dosed = station.dose_recipe(confirmed["task_id"])

    # CMD_SAMPLE carries the PLC's composite scan/dose/bead operation.  The
    # bead count must be transmitted, even though the graph exposes recipe
    # confirmation as a separate supervisory node.
    assert station.station.controller.transport.last_command[80] == 80
    started = station.start_resonance(dosed["task_id"])
    resonated = station.unload_resonance(started["task_id"])
    scanned = station.scan_bottle(resonated["task_id"])
    bottled = station.prepare_post(scanned["task_id"], qrcode=scanned["qrcode"])
    assigned = station.assign_small_crucible(bottled["post_id"], bottled["lot_id"])
    scheduled = station.confirm_firing_schedule(bottled["post_id"], assigned["lot_id"])
    sintering = station.start_sintering(bottled["post_id"])
    unloaded = station.unload_sintered(bottled["post_id"])

    assert [
        confirmed["state"],
        dosed["state"],
        started["state"],
        resonated["state"],
        scanned["state"],
        bottled["state"],
        assigned["state"],
        scheduled["state"],
        sintering["state"],
        unloaded["state"],
    ] == [
        "RECIPE_CONFIRMED",
        "DOSED",
        "RESONANCE_READY",
        "RESONATED",
        "BOTTLE_SCANNED",
        "BOTTLED",
        "ASSIGNED",
        "SCHEDULED",
        "SINTERING",
        "UNLOADED",
    ]
    assert _record(station, unloaded["small_cubics"][0])["location"] == "stock_buffer"
