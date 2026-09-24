"""Simulation checks for rejected parameters, disconnect, and no command retry."""

import pytest

from yb_sse_devices.devices.yb_synthesis_modbus_station.device import (
    YBSynthesisModbusStation,
)
from yb_sse_devices.devices.yb_synthesis_modbus_station.modbus import (
    ModbusConnectionError,
    ModbusTimeoutError,
)


def test_real_mode_requires_a_graph_address() -> None:
    with pytest.raises(ValueError, match="启动图"):
        YBSynthesisModbusStation(simulation=False, ip="")


def test_out_of_range_parameter_does_not_write() -> None:
    station = YBSynthesisModbusStation(
        simulation=True,
        business_simulation=False,
        simulation_handshake_delay=0.05,
    )
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    try:
        with pytest.raises(ValueError):
            station.run_acoustic_load(
                acceleration=131, frequency=1, duration_minutes=1, timeout=1
            )
        assert writes == []
    finally:
        station.close()


def test_timeout_does_not_send_the_command_again() -> None:
    station = YBSynthesisModbusStation(
        simulation=True,
        business_simulation=False,
        simulation_handshake_delay=0.05,
    )
    writes: list[tuple[int, ...]] = []
    original = station.controller.client.write_command

    def capture(values):
        writes.append(tuple(int(item) for item in values))
        return original(values)

    station.controller.client.write_command = capture
    station.controller.advance = lambda *_args, **_kwargs: None
    try:
        with pytest.raises(TimeoutError):
            station.run_acoustic_load(
                acceleration=1, frequency=1, duration_minutes=1, timeout=0.35
            )
        assert len(writes) == 1
    finally:
        station.close()


def test_unreachable_plc_does_not_retry_the_command() -> None:
    attempts = {"count": 0}

    def refuse(*_args, **_kwargs):
        attempts["count"] += 1
        raise TimeoutError("timed out")

    station = YBSynthesisModbusStation(
        simulation=False,
        business_simulation=False,
        ip="127.0.0.1",
        port=9,
        config={"timeout": 0.2},
    )
    station.controller.transport._socket_factory = refuse
    try:
        with pytest.raises((ModbusTimeoutError, ModbusConnectionError, TimeoutError)):
            station.run_acoustic_load(
                acceleration=1, frequency=1, duration_minutes=1, timeout=0.5
            )
        assert attempts["count"] == 1
    finally:
        station.close()
