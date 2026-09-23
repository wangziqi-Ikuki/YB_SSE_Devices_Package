from yb_sse_devices.devices.yb_synthesis_modbus_station.device import (
    YBSynthesisModbusStation,
)


def _station() -> YBSynthesisModbusStation:
    return YBSynthesisModbusStation(
        simulation=True,
        business_simulation=False,
        simulation_handshake_delay=0.05,
        simulation_dosing_duration=0.05,
    )


def test_constant_joule_heating_returns_to_the_same_standby_slot() -> None:
    station = _station()
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    try:
        heated = station.run_joule_heating(
            carrier_type='椭圆石墨舟',
            heating_mode='恒温',
            pickup_positions=[2, 5],
            constant_temperature=500,
            constant_hold_minutes=2,
            timeout=5,
        )
        loads = [item for item in writes if item[0] == 4]
        assert [item[61] for item in loads] == [2, 5]
        assert loads[0][62:66] == (1, 1, 500, 120)
        assert heated['success'] is True

        unloaded = station.unload_joule_heating(
            carrier_type='椭圆石墨舟',
            pickup_positions=[2, 5],
            timeout=5,
        )
        unloads = [item for item in writes if item[0] == 5]
        assert [item[9:11] for item in unloads] == [(1, 2), (1, 5)]
        assert unloaded['success'] is True
    finally:
        station.close()


def test_graphite_plate_heats_without_a_robot_move() -> None:
    station = _station()
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    try:
        heated = station.run_joule_heating(
            carrier_type='石墨板',
            heating_mode='恒温',
            constant_temperature=400,
            constant_hold_minutes=1,
            timeout=5,
        )
        assert writes[-1][0] == 4
        assert writes[-1][61:64] == (0, 1, 3)
        assert heated['success'] is True
        before = len(writes)
        unloaded = station.unload_joule_heating(carrier_type='石墨板', timeout=5)
        assert len(writes) == before
        assert unloaded['success'] is True
    finally:
        station.close()
