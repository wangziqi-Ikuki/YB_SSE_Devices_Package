from __future__ import annotations

import json

import pytest

from yb_sse_devices import YBSynthesisAtomicStation
from yb_sse_devices.devices.yb_synthesis_modbus_station.controller import (
    SynthesisDirectController,
)
from yb_sse_devices.simulation.modbus_transport import SynthesisSimulationTransport


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


def test_direct_plc_chain_keeps_os_task_local_and_uses_command7_then_command3() -> None:
    """Exercise the production branch against a Modbus-only dry transport."""

    station = YBSynthesisAtomicStation(
        config={"simulation": False, "business_simulation": False},
        action_timeout=3,
        simulation_step=0.01,
    )
    transport = SynthesisSimulationTransport(
        handshake_delay=0.01, crucible_id="CRU-DIRECT"
    )
    station.station.controller = SynthesisDirectController(
        transport, unit_id=1, plc_units=True
    )
    try:
        created = station.create_batch(
            task_slot_nums=[1],
            pallet_slot_types=[1, 0, 0, 0, 0, 0],
            powder_rack_positions=[21, 22, 23, 24],
        )
        assert created["task_id"].startswith("OS-TASK-")

        loaded = station.load_big_crucible(
            created["task_id"], pallet_slot_types=[1, 0, 0, 0, 0, 0]
        )
        assert transport.writes[0][1][:4] == (7, 2, 1, 1)
        assert transport.writes[0][1][10] == 2  # rack2 bead-bottle wire source

        dosed = station.dose_recipe(
            loaded["task_id"],
            powder_rack_positions=[21, 22, 23, 24],
            crucible_return_positions=[12],
        )
        assert transport.last_command[0] == 3
        assert transport.last_command[1:3] == (1, 12)
        assert dosed["sampling_results"][0]["material_count"] == 3
        assert dosed["sampling_results"][0]["weights"] == [0.9, 1.2, 0.88]
        assert '"location": "synthesis_crucible_rack_01"' in dosed["ledger_json"]
    finally:
        station.close()


def test_direct_target_chain_returns_six_slot_alumina_without_beads() -> None:
    """The requested rack-2 -> rack-1, two-powder target stays PLC-only."""

    station = YBSynthesisAtomicStation(
        config={"simulation": False, "business_simulation": False},
        action_timeout=3,
        simulation_step=0.01,
    )
    transport = SynthesisSimulationTransport(
        handshake_delay=0.01, crucible_id="CRU-TARGET"
    )
    station.station.controller = SynthesisDirectController(
        transport, unit_id=1, plc_units=True
    )
    try:
        created = station.create_batch(
            recipe_name="YB-LiCl-P2S5-2G",
            formula="LiCl-P2S5",
            synthesis_mass=4.0,
            n_ball_bead=0,
            powder_names=["LiCl", "P2S5"],
            powder_weights=[2.0, 2.0],
            powder_tolerances=[0.0007, 0.0007],
            powder_pre_adds=[False, False],
            pallet_type=3,
            cubic_type=1,
            task_slot_nums=[3, 4],
            has_bead_bottle=False,
            bead_count=0,
            fetch_cubic_source=1,
            pallet_slot_types=[0, 0, 1, 1, 0, 0],
            powder_rack_positions=[31, 32],
        )
        loaded = station.load_big_crucible(
            created["task_id"],
            fetch_cubic_source=1,
            pallet_type=3,
            pallet_slot_types=[0, 0, 1, 1, 0, 0],
            bead_source=0,
        )
        assert transport.writes[0][1] == (7, 2, 1, 3, 0, 0, 1, 1, 0, 0, 0)

        dosed = station.dose_recipe(
            loaded["task_id"],
            powder_rack_positions=[31, 32],
            crucible_return_positions=[3, 4],
        )
        assert transport.last_command[0] == 3
        assert transport.last_command[1:3] == (4, 4)
        assert transport.last_command[80] == 0
        assert dosed["sampling_results"][-1]["material_count"] == 2
        assert dosed["sampling_results"][-1]["weights"] == [2.0, 2.0]
        assert '"location": "synthesis_crucible_rack_01"' in dosed["ledger_json"]
        assert all(
            record["kind"] != "ball_beads"
            for record in json.loads(dosed["ledger_json"])
        )
    finally:
        station.close()
