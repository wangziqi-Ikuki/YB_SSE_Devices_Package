"""Minimal Modbus TCP server backed by the package-local YB PLC model.

The server is intentionally small and deterministic.  It exposes the same
holding-register subset used by :class:`ModbusTcpTransport` (functions 03,
06, and 16), while a background clock advances the exact same
``SynthesisSimulationTransport`` used by the in-process dry-run.

Run it with::

    python -m yb_sse_devices.simulation.modbus_server --host 127.0.0.1 --port 5020

The production device package never starts this server automatically.  It is
an integration-test PLC endpoint for ``deployment/graphs/integration.json``.
"""

from __future__ import annotations

import argparse
import socketserver
import struct
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from yb_sse_devices.simulation.modbus_transport import SynthesisSimulationTransport
from yb_sse_devices.simulation.plc_model import SynthesisPlcModel


class ModbusSimProtocolError(ValueError):
    """A malformed Modbus TCP request with a Modbus exception code."""

    def __init__(self, code: int, message: str) -> None:
        self.code = int(code)
        super().__init__(message)


def _recv_exact(stream: Any, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Modbus TCP peer closed the connection")
        data.extend(chunk)
    return bytes(data)


class _ModbusRequestHandler(socketserver.BaseRequestHandler):
    server: "_ThreadingModbusServer"

    def handle(self) -> None:
        self.request.settimeout(self.server.request_timeout)
        while not self.server.stopping.is_set():
            try:
                header = _recv_exact(self.request, 7)
            except (ConnectionError, OSError, TimeoutError):
                return
            transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
            if length < 2 or length > 254:
                return
            try:
                pdu = _recv_exact(self.request, length - 1)
                response_pdu = self.server.process_pdu(protocol_id, unit_id, pdu)
            except ModbusSimProtocolError as exc:
                function = pdu[0] if "pdu" in locals() and pdu else 0
                response_pdu = struct.pack(">BB", (function & 0x7F) | 0x80, exc.code)
            except Exception:
                function = pdu[0] if "pdu" in locals() and pdu else 0
                response_pdu = struct.pack(">BB", (function & 0x7F) | 0x80, 4)
            response = struct.pack(">HHHB", transaction_id, 0, len(response_pdu) + 1, unit_id)
            try:
                self.request.sendall(response + response_pdu)
            except OSError:
                return


class _ThreadingModbusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], owner: "ModbusTcpSimulator") -> None:
        self.owner = owner
        self.request_timeout = owner.request_timeout
        self.stopping = owner.stopping
        super().__init__(address, _ModbusRequestHandler)

    def process_pdu(self, protocol_id: int, unit_id: int, pdu: bytes) -> bytes:
        return self.owner.process_pdu(protocol_id, unit_id, pdu)


class ModbusTcpSimulator:
    """Threaded Modbus TCP endpoint for the YB synthesis PLC model."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5020,
        *,
        unit_id: int = 1,
        crucible_id: str = "CRU-SIM-001",
        material_names: Sequence[str] | None = None,
        dosing_duration: float = 1.0,
        handshake_delay: float = 0.1,
        failure_plan: Mapping[str, str] | None = None,
        tick_interval: float = 0.05,
        request_timeout: float = 5.0,
    ) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if not 0 <= int(unit_id) <= 247:
            raise ValueError("unit_id must be in the range 0..247")
        if float(tick_interval) <= 0:
            raise ValueError("tick_interval must be positive")
        if float(request_timeout) <= 0:
            raise ValueError("request_timeout must be positive")
        self.host = str(host)
        self.requested_port = int(port)
        self.unit_id = int(unit_id)
        self.tick_interval = float(tick_interval)
        self.request_timeout = float(request_timeout)
        self.stopping = threading.Event()
        self._lock = threading.RLock()
        self.model = SynthesisPlcModel(
            dosing_duration=float(dosing_duration),
            auto_scan_id=str(crucible_id).strip(),
        )
        self.transport = SynthesisSimulationTransport(
            self.model,
            crucible_id=crucible_id,
            material_names=material_names,
            handshake_delay=handshake_delay,
            failure_plan=failure_plan,
        )
        self.transport.connect()
        self._server = _ThreadingModbusServer((self.host, self.requested_port), self)
        self._clock_thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    def start(self) -> None:
        """Start the TCP listener and deterministic wall-clock adapter."""

        if self._clock_thread and self._clock_thread.is_alive():
            return
        self.stopping.clear()
        self._clock_thread = threading.Thread(
            target=self._advance_clock,
            name="yb-modbus-sim-clock",
            daemon=True,
        )
        self._clock_thread.start()
        threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="yb-modbus-sim-server",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.stopping.set()
        self._server.shutdown()
        self._server.server_close()
        if self._clock_thread:
            self._clock_thread.join(timeout=2.0)
        with self._lock:
            self.transport.close()

    close = stop

    def __enter__(self) -> "ModbusTcpSimulator":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def _advance_clock(self) -> None:
        previous = time.monotonic()
        while not self.stopping.wait(self.tick_interval):
            current = time.monotonic()
            elapsed = max(0.0, current - previous)
            previous = current
            with self._lock:
                self.transport.advance(elapsed)

    def process_pdu(self, protocol_id: int, unit_id: int, pdu: bytes) -> bytes:
        if protocol_id != 0:
            raise ModbusSimProtocolError(1, "unsupported Modbus protocol")
        if unit_id != self.unit_id:
            raise ModbusSimProtocolError(11, "unit id is not configured for this simulator")
        if not pdu:
            raise ModbusSimProtocolError(3, "empty PDU")
        function = pdu[0]
        with self._lock:
            if function == 3:
                return self._read_holding_registers(pdu)
            if function == 6:
                return self._write_single_register(pdu)
            if function == 16:
                return self._write_multiple_registers(pdu)
        raise ModbusSimProtocolError(1, f"unsupported function code: {function}")

    def _read_holding_registers(self, pdu: bytes) -> bytes:
        if len(pdu) != 5:
            raise ModbusSimProtocolError(3, "malformed read request")
        _, offset, quantity = struct.unpack(">BHH", pdu)
        if not 1 <= quantity <= 125:
            raise ModbusSimProtocolError(3, "read quantity out of range")
        address = 40001 + offset
        values = self.transport.read_holding_registers(
            address, quantity, unit_id=self.unit_id
        )
        return struct.pack(">BB", 3, quantity * 2) + struct.pack(
            f">{quantity}H", *values
        )

    def _write_single_register(self, pdu: bytes) -> bytes:
        if len(pdu) != 5:
            raise ModbusSimProtocolError(3, "malformed single-write request")
        _, offset, value = struct.unpack(">BHH", pdu)
        self.transport.write_holding_register(40001 + offset, value, unit_id=self.unit_id)
        return pdu

    def _write_multiple_registers(self, pdu: bytes) -> bytes:
        if len(pdu) < 6:
            raise ModbusSimProtocolError(3, "malformed multiple-write request")
        _, offset, quantity, byte_count = struct.unpack(">BHHB", pdu[:6])
        if not 1 <= quantity <= 123 or byte_count != quantity * 2:
            raise ModbusSimProtocolError(3, "write quantity or byte count is invalid")
        if len(pdu) != 6 + byte_count:
            raise ModbusSimProtocolError(3, "write payload length is invalid")
        values = struct.unpack(f">{quantity}H", pdu[6:])
        self.transport.write_holding_registers(
            40001 + offset, values, unit_id=self.unit_id
        )
        return struct.pack(">BHH", 16, offset, quantity)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YB 合成工站 Modbus TCP 仿真 PLC")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--crucible-id", default="CRU-SIM-001")
    parser.add_argument("--dosing-duration", type=float, default=1.0)
    parser.add_argument("--handshake-delay", type=float, default=0.1)
    parser.add_argument("--tick-interval", type=float, default=0.05)
    args = parser.parse_args(argv)
    server = ModbusTcpSimulator(
        host=args.host,
        port=args.port,
        unit_id=args.unit_id,
        crucible_id=args.crucible_id,
        dosing_duration=args.dosing_duration,
        handshake_delay=args.handshake_delay,
        tick_interval=args.tick_interval,
    )
    server.start()
    print(f"YB Modbus simulator listening on {server.host}:{server.port}", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        server.stop()


__all__ = ["ModbusSimProtocolError", "ModbusTcpSimulator", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
