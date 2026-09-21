from __future__ import annotations

import json
import struct

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


def test_modbus_simulator_records_structured_request_response_and_stage(tmp_path) -> None:
    trace_path = tmp_path / "modbus.jsonl"
    server = ModbusTcpSimulator(
        port=0,
        handshake_delay=0.1,
        tick_interval=0.01,
        trace_file=trace_path,
    )
    with server:
        # Direct PDU calls are supported for simulator harnesses and preserve
        # the old process_pdu API; the optional transaction id makes TCP traces
        # easy to match in a UI.
        response = server.process_pdu(
            0,
            1,
            struct.pack(">BHH", 3, 99, 17),
            transaction_id=41,
        )
        assert response[0:2] == bytes((3, 34))
        record = server.get_communication_log()[-1]
        assert record["transaction_id"] == 41
        assert record["ok"] is True
        assert record["request"]["function_name"] == "read_holding_registers"
        assert record["request"]["address"] == 40100
        assert record["request"]["quantity"] == 17
        assert record["response"]["values"] == list(struct.unpack(">17H", response[2:]))
        assert record["state"]["stage"] == "idle"
        assert record["state"]["status_registers"]["40102"] == 0

        write_response = server.process_pdu(
            0,
            1,
            struct.pack(">BHH", 6, 0, 1),
            transaction_id=43,
        )
        assert write_response == struct.pack(">BHH", 6, 0, 1)
        command_record = server.get_communication_log()[-1]
        assert command_record["request"]["address"] == 40001
        assert command_record["request"]["values"] == [1]
        assert command_record["state"]["operation"]["command"] == "down_material"
        assert command_record["state"]["stage"] == "robot_permission"
        server.transport.advance(1.0)
        server.process_pdu(
            0,
            1,
            struct.pack(">BHH", 3, 99, 17),
            transaction_id=44,
        )
        completed_record = server.get_communication_log()[-1]
        assert completed_record["state"]["stage"] == "completed"
        assert completed_record["state"]["status_registers"]["40100"] == 2

        # A malformed request is also retained with a useful protocol error
        # rather than disappearing from the evidence used to decide whether a
        # run passed.
        with pytest.raises(Exception):
            server.process_pdu(0, 1, bytes((3, 0)), transaction_id=42)
        failed = server.get_communication_log()[-1]
        assert failed["transaction_id"] == 42
        assert failed["ok"] is False
        assert failed["error"]["exception_code"] == 3
        lines = trace_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4
        assert json.loads(lines[-1])["transaction_id"] == 42
