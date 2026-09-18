from __future__ import annotations

import pytest

from yb_sse_devices.simulation.plc_model import SimulationError
from yb_sse_devices.synthesis_modbus_station import YBSynthesisModbusStation


def _station() -> YBSynthesisModbusStation:
    return YBSynthesisModbusStation(
        config={
            "simulation": True,
            "simulation_crucible_id": "CRU-SIM-001",
            "simulation_dosing_duration": 1.0,
            "material_names": ["Li2S"],
        }
    )


def test_material_action_binds_resource_and_keeps_business_task_id() -> None:
    station = _station()
    result = station.sample_with_materials(
        crucible={"barcode": "CRU-SIM-001"},
        source_site="synthesis_crucible_rack_01-0",
        task_id="BUSINESS-TASK-001",
        slot_num=1,
        rack_positions=[21],
        masses=[0.9],
        tolerances=[0.0007],
        material_names=["Li2S"],
    )

    assert result["success"] is True
    assert result["task_id"] == "BUSINESS-TASK-001"
    assert result["qr_code"] == "CRU-SIM-001"
    assert result["weights"] == pytest.approx([0.9])
    assert result["result_codes"] == [1]


def test_material_action_rejects_site_and_plc_slot_mismatch() -> None:
    station = _station()
    with pytest.raises(ValueError, match="PLC 槽位"):
        station.sample_with_materials(
            crucible={"barcode": "CRU-SIM-001"},
            source_site="synthesis_crucible_rack_01-1",
            slot_num=1,
        )


def test_pause_freezes_simulated_process_until_resume() -> None:
    station = _station()
    station.start_sampling(
        task_id="BUSINESS-TASK-002",
        slot_num=1,
        rack_positions=[21],
        masses=[0.9],
        tolerances=[0.0007],
        material_names=["Li2S"],
    )
    station.pause()
    station.advance_simulation(2.0)
    assert station.sampling == 1
    assert station.status == "PAUSED"
    station.resume()
    station.advance_simulation(1.0)
    assert station.sampling == 2


def test_clear_fault_and_reset_restore_simulation() -> None:
    station = _station()
    station.inject_simulation_fault("scanner", once=True)
    station.start_sampling(
        task_id="BUSINESS-TASK-003",
        slot_num=1,
        rack_positions=[21],
        masses=[0.9],
        tolerances=[0.0007],
        material_names=["Li2S"],
    )
    assert station.status == "FAULT"
    station.clear_fault()
    assert station.status == "IDLE"
    station.reset()
    assert station.status == "IDLE"


def test_simulation_records_handshake_and_rejects_overlapping_action() -> None:
    station = _station()
    station.start_sampling(
        task_id="BUSINESS-TASK-004",
        slot_num=1,
        rack_positions=[21],
        masses=[0.9],
        tolerances=[0.0007],
        material_names=["Li2S"],
    )
    transport = station.controller.transport
    phases = [entry["phase"] for entry in transport.handshake_history]
    assert phases[:2] == ["parameters_written", "accepted"]
    with pytest.raises(SimulationError, match="动作互斥"):
        station.down_material()


def test_configured_handshake_failure_is_observable_and_resettable() -> None:
    station = YBSynthesisModbusStation(
        config={
            "simulation": True,
            "simulation_crucible_id": "CRU-SIM-001",
            "simulation_failure_plan": {
                "sample_add_powder_and_beads": "扫码器握手失败"
            },
        }
    )
    started = station.start_sampling(
        task_id="BUSINESS-TASK-005",
        slot_num=1,
        rack_positions=[21],
        masses=[0.9],
        tolerances=[0.0007],
        material_names=["Li2S"],
    )
    assert started["success"] is False
    assert station.status == "FAULT"
    assert station.controller.transport.handshake_history[-1]["phase"] == "failed"
    station.reset()
    assert station.status == "IDLE"
