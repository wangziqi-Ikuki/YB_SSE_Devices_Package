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
        written = len(writes)
        finished = station.wait_acoustic_process(timeout=5)
        assert len(writes) == written
        assert finished["success"] is True

        unloaded = station.run_acoustic_unload(timeout=5)
        assert writes[-1][:3] == (9, 1, 1)
        assert unloaded["success"] is True
    finally:
        station.close()


def test_each_slot_can_use_a_different_recipe() -> None:
    station = _station()
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    recipes = ["", "", "20260920-Li5.3P1.0S4.3Cl1.0Br0.7-R1", "20260920-Li5.5P1.0S4.5Cl0.8Br0.7-R2", "", ""]
    try:
        loaded = station.load_pallet_from_rack2(
            pallet_type="6 槽位托盘",
            cubic_type="Al2O3 30*30",
            slot_recipes=recipes,
            timeout=5,
        )
        command7 = writes[0]
        assert command7[0] == 7
        assert command7[1:4] == (2, 1, 3)
        assert command7[4:10] == (0, 0, 1, 1, 0, 0)
        assert loaded["success"] is True

        dosed = station.dose_selected_slots(
            pallet_type="6 槽位托盘",
            cubic_type="Al2O3 30*30",
            slot_recipes=recipes,
            timeout=5,
        )
        dosing = [item for item in writes if item[0] == 3]
        assert [item[1:3] for item in dosing] == [(3, 3), (4, 4)]
        assert abs(decode_float32(dosing[0][4:6]) - 0.7136) < 1e-4
        assert abs(decode_float32(dosing[1][4:6]) - 0.5695) < 1e-4
        assert dosed["slot_nums"] == [3, 4]
    finally:
        station.close()


def test_four_and_five_slot_trays_keep_per_slot_recipes() -> None:
    cases = (
        (
            "4 槽位托盘",
            "Al2O3 40*40",
            ["", "20260920-Li5.3P1.0S4.3Cl1.0Br0.7-R1", "", "20260920-Li5.5P1.0S4.5Cl0.8Br0.7-R2"],
            (2, 1, 1),
            (0, 3, 0, 3, 0, 0),
            [(2, 2), (4, 4)],
        ),
        (
            "5 槽位托盘",
            "Al2O3 35*40",
            ["20260920-Li5.3P1.0S4.3Cl1.0Br0.7-R1", "", "", "", "20260920-Li5.5P1.0S4.5Cl0.8Br0.7-R2"],
            (2, 1, 2),
            (2, 0, 0, 0, 2, 0),
            [(1, 1), (5, 5)],
        ),
    )
    for pallet_type, cubic_type, recipes, command_head, slot_types, dose_slots in cases:
        station = _station()
        writes: list[tuple[int, ...]] = []
        original = station.controller.client.write_command

        def capture(values, writes=writes, original=original):
            writes.append(tuple(int(item) for item in values))
            return original(values)

        station.controller.client.write_command = capture
        try:
            station.load_pallet_from_rack2(
                pallet_type=pallet_type,
                cubic_type=cubic_type,
                slot_recipes=recipes,
                timeout=5,
            )
            assert writes[0][0] == 7
            assert writes[0][1:4] == command_head
            assert writes[0][4:10] == slot_types
            station.dose_selected_slots(
                pallet_type=pallet_type,
                cubic_type=cubic_type,
                slot_recipes=recipes,
                timeout=5,
            )
            dosing = [item for item in writes if item[0] == 3]
            assert [item[1:3] for item in dosing] == dose_slots
            assert abs(decode_float32(dosing[0][4:6]) - 0.7136) < 1e-4
            assert abs(decode_float32(dosing[1][4:6]) - 0.5695) < 1e-4
        finally:
            station.close()


def test_muffle_slot_two_writes_furnace_two_and_returns_to_the_same_slot() -> None:
    station = _station()
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    segments = '[{"temperature": 800, "minutes": 30}, {"temperature": 900, "minutes": 60}]'
    try:
        loaded = station.run_muffle_load(quartz_slot=2, furnace_segments=segments, timeout=5)
        command4 = writes[0]
        assert command4[0] == 4
        assert command4[16] == 1
        assert command4[17:19] == (800, 900)
        assert command4[23:25] == (30, 60)
        assert loaded["success"] is True
        written = len(writes)
        finished = station.wait_muffle_process(quartz_slot=2, timeout=5)
        assert len(writes) == written
        assert finished["success"] is True
        unloaded = station.run_muffle_unload(quartz_slot=2, timeout=5)
        assert writes[-1][:3] == (5, 2, 2)
        assert unloaded["success"] is True
    finally:
        station.close()
