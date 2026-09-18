from __future__ import annotations

import pytest

from yb_sse_devices.simulation.modbus_server import ModbusTcpSimulator
from yb_sse_devices.synthesis_direct import SynthesisDirectController
from yb_sse_devices.synthesis_modbus import ModbusTcpTransport


def test_real_modbus_tcp_client_completes_sampling_against_simulator() -> None:
    server = ModbusTcpSimulator(
        port=0,
        crucible_id="CRU-TCP-001",
        material_names=("Li2S",),
        dosing_duration=0.1,
        handshake_delay=0.02,
        tick_interval=0.01,
    )
    with server:
        transport = ModbusTcpTransport(server.host, server.port, timeout=1.0)
        controller = SynthesisDirectController(transport, material_names=("Li2S",))
        controller.connect()
        try:
            result = controller.run_sampling(
                task_id="BUSINESS-TCP-001",
                slot_num=1,
                rack_positions=(21,),
                masses=(0.9,),
                tolerances=(0.0007,),
                material_names=("Li2S",),
                timeout=2.0,
                step=0.02,
            )
            assert result["task_id"] == "BUSINESS-TCP-001"
            assert result["result"]["qr_code"] == "CRU-TCP-001"
            assert result["result"]["weights"]["Li2S"] == pytest.approx(0.9)
            assert result["result"]["results"] == {"Li2S": 1}
        finally:
            controller.close()


def test_modbus_tcp_simulator_exposes_pause_register_and_holds_clock() -> None:
    server = ModbusTcpSimulator(
        port=0,
        dosing_duration=0.2,
        tick_interval=0.01,
    )
    with server:
        transport = ModbusTcpTransport(server.host, server.port, timeout=1.0)
        controller = SynthesisDirectController(transport, material_names=("Li2S",))
        controller.connect()
        try:
            controller.start_sampling(
                task_id="BUSINESS-TCP-002",
                slot_num=1,
                rack_positions=(21,),
                masses=(0.9,),
                tolerances=(0.0007,),
                material_names=("Li2S",),
            )
            controller.pause()
            status = controller.status()
            assert status["pause"] == 1
            assert status["sampling"] == 1
            controller.resume()
        finally:
            controller.close()
