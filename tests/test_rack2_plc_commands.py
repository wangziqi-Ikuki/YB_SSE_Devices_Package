"""Rack-2 command payload check. In-process registers only."""

from __future__ import annotations

from yb_sse_devices.devices.yb_synthesis_modbus_station.device import (
    YBSynthesisModbusStation,
)
from yb_sse_devices.devices.yb_synthesis_modbus_station.modbus import decode_float32


def _station() -> YBSynthesisModbusStation:
    return YBSynthesisModbusStation(
        simulation=True,
        business_simulation=False,
        simulation_handshake_delay=0.05,
        simulation_dosing_duration=0.05,
    )


def test_rack2_actions_write_command7_then_command3() -> None:
    station = _station()
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    try:
        assert station.business_state is None
        loaded = station.load_pallet_from_rack2(
            pallet_type="6 槽位托盘",
            cubic_type="Al2O3 30*30",
            task_slot_nums=[1],
            has_bead_bottle=False,
            bead_count=0,
            timeout=5,
        )
        command7 = writes[0]
        assert command7[0] == 7
        assert command7[1:4] == (2, 1, 3)
        assert command7[4:10] == (1, 0, 0, 0, 0, 0)
        assert command7[10] == 0
        assert loaded["success"] is True
        assert loaded["command"] == "fetch_cubic"

        dosed = station.dose_selected_slots(
            recipe_name="20260920-Li5.3P1.0S4.3Cl1.0Br0.7-R1",
            pallet_type="6 槽位托盘",
            cubic_type="Al2O3 30*30",
            task_slot_nums=[1],
            has_bead_bottle=False,
            bead_count=0,
            timeout=5,
        )
        command3 = writes[-1]
        assert command3[0] == 3
        assert command3[1:3] == (1, 1)
        assert command3[3] == 1
        assert abs(decode_float32(command3[4:6]) - 0.7136) < 1e-4
        assert command3[8] == 4
        assert command3[13] == 2
        assert command3[18] == 3
        assert command3[79:81] == (1, 0)
        assert dosed["success"] is True
        assert dosed["slot_nums"] == [1]
        assert len(dosed["weights"]) == 4

        resonated = station.run_acoustic_load(
            acceleration=2, frequency=3, duration_minutes=4, timeout=5
        )
        command8 = writes[-1]
        assert command8[0] == 8
        assert command8[1:6] == (1, 2, 2, 3, 240)
        assert resonated["success"] is True

        unloaded = station.run_acoustic_unload(timeout=5)
        assert writes[-1][:3] == (9, 1, 1)
        assert unloaded["success"] is True
    finally:
        station.close()
