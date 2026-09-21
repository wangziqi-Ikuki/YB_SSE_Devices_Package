"""Minimal Modbus TCP server backed by the package-local YB PLC model.

The server is intentionally small and deterministic.  It exposes the same
holding-register subset used by :class:`ModbusTcpTransport` (functions 03,
06, and 16), while a background clock advances the exact same
``SynthesisSimulationTransport`` used by the in-process dry-run.

Run it with::

    python -m yb_sse_devices.simulation.modbus_server --host 127.0.0.1 --port 5020

Add ``--log-file /tmp/yb-modbus-sim.jsonl`` to retain a structured trace of
every request/response.  ``--verbose`` mirrors the same JSON records to stdout.

The production device package never starts this server automatically.  It is
an integration-test PLC endpoint for ``deployment/graphs/integration.json``.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import socketserver
import struct
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from yb_sse_devices.simulation.modbus_transport import SynthesisSimulationTransport
from yb_sse_devices.simulation.plc_model import SynthesisPlcModel


class ModbusSimProtocolError(ValueError):
    """A malformed Modbus TCP request with a Modbus exception code."""

    def __init__(self, code: int, message: str) -> None:
        self.code = int(code)
        super().__init__(message)


TraceSink = Callable[[Mapping[str, object]], None]


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
                response_pdu = self.server.process_pdu(
                    protocol_id,
                    unit_id,
                    pdu,
                    transaction_id=transaction_id,
                )
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

    def process_pdu(
        self,
        protocol_id: int,
        unit_id: int,
        pdu: bytes,
        *,
        transaction_id: int | None = None,
    ) -> bytes:
        return self.owner.process_pdu(
            protocol_id,
            unit_id,
            pdu,
            transaction_id=transaction_id,
        )


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
        request_timeout: float = 3600.0,
        communication_history_limit: int = 2000,
        trace_file: str | pathlib.Path | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if not 0 <= int(unit_id) <= 247:
            raise ValueError("unit_id must be in the range 0..247")
        if float(tick_interval) <= 0:
            raise ValueError("tick_interval must be positive")
        if float(request_timeout) <= 0:
            raise ValueError("request_timeout must be positive")
        if int(communication_history_limit) < 1:
            raise ValueError("communication_history_limit must be positive")
        self.host = str(host)
        self.requested_port = int(port)
        self.unit_id = int(unit_id)
        self.tick_interval = float(tick_interval)
        self.request_timeout = float(request_timeout)
        self.communication_history_limit = int(communication_history_limit)
        self.trace_sink = trace_sink
        self.trace_file = pathlib.Path(trace_file) if trace_file else None
        self._trace_stream: Any | None = None
        if self.trace_file is not None:
            self.trace_file.parent.mkdir(parents=True, exist_ok=True)
            self._trace_stream = self.trace_file.open("a", encoding="utf-8")
        self.stopping = threading.Event()
        self._lock = threading.RLock()
        # JSON-compatible communication records.  Keeping this on the server
        # makes the simulator inspectable by a desktop UI without requiring a
        # second API or changing the Modbus wire protocol.
        self.communication_log: list[dict[str, object]] = []
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
            if self._trace_stream is not None:
                self._trace_stream.close()
                self._trace_stream = None

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

    def process_pdu(
        self,
        protocol_id: int,
        unit_id: int,
        pdu: bytes,
        *,
        transaction_id: int | None = None,
    ) -> bytes:
        """Process one PDU and append a structured request/response trace.

        ``transaction_id`` is optional for source compatibility with callers
        that used ``process_pdu`` directly before tracing was added.  TCP
        callers pass it so a request can be matched to its response in a log
        viewer.  Failed protocol requests are logged as well, including the
        simulator's rejection reason and the PLC stage at that moment.
        """

        started = time.monotonic()
        request = self._describe_request(pdu)
        try:
            if protocol_id != 0:
                raise ModbusSimProtocolError(1, "unsupported Modbus protocol")
            if unit_id != self.unit_id:
                raise ModbusSimProtocolError(
                    11, "unit id is not configured for this simulator"
                )
            if not pdu:
                raise ModbusSimProtocolError(3, "empty PDU")
            function = pdu[0]
            with self._lock:
                if function == 3:
                    response = self._read_holding_registers(pdu)
                elif function == 6:
                    response = self._write_single_register(pdu)
                elif function == 16:
                    response = self._write_multiple_registers(pdu)
                else:
                    raise ModbusSimProtocolError(
                        1, f"unsupported function code: {function}"
                    )
        except Exception as exc:
            self._record_communication(
                transaction_id=transaction_id,
                protocol_id=protocol_id,
                unit_id=unit_id,
                request=request,
                response=None,
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "exception_code": getattr(exc, "code", None),
                },
                started=started,
            )
            raise
        self._record_communication(
            transaction_id=transaction_id,
            protocol_id=protocol_id,
            unit_id=unit_id,
            request=request,
            response=self._describe_response(response),
            error=None,
            started=started,
        )
        return response

    @staticmethod
    def _describe_request(pdu: bytes) -> dict[str, object]:
        """Decode the fields useful to a human inspecting a trace."""

        if not pdu:
            return {"function": None, "function_name": "empty", "raw_hex": ""}
        function = int(pdu[0])
        names = {3: "read_holding_registers", 6: "write_single_register", 16: "write_multiple_registers"}
        result: dict[str, object] = {
            "function": function,
            "function_name": names.get(function, "unknown"),
            "raw_hex": pdu.hex(),
        }
        try:
            if function == 3 and len(pdu) == 5:
                _, offset, quantity = struct.unpack(">BHH", pdu)
                result.update({"address": 40001 + offset, "quantity": quantity})
            elif function == 6 and len(pdu) == 5:
                _, offset, value = struct.unpack(">BHH", pdu)
                result.update({"address": 40001 + offset, "values": [value]})
            elif function == 16 and len(pdu) >= 6:
                _, offset, quantity, byte_count = struct.unpack(">BHHB", pdu[:6])
                result.update({"address": 40001 + offset, "quantity": quantity})
                if byte_count == quantity * 2 and len(pdu) == 6 + byte_count:
                    result["values"] = list(struct.unpack(f">{quantity}H", pdu[6:]))
        except (struct.error, ValueError):
            # The normal protocol validation records the actual error.  Keep
            # the trace itself JSON serializable even for malformed frames.
            pass
        return result

    @staticmethod
    def _describe_response(pdu: bytes) -> dict[str, object]:
        result: dict[str, object] = {"function": int(pdu[0]) if pdu else None, "raw_hex": pdu.hex()}
        if not pdu:
            return result
        function = int(pdu[0])
        if function == 3 and len(pdu) >= 2 and pdu[1] == len(pdu) - 2:
            byte_count = int(pdu[1])
            if byte_count % 2 == 0 and len(pdu) == byte_count + 2:
                result["values"] = list(struct.unpack(f">{byte_count // 2}H", pdu[2:]))
        elif function == 6 and len(pdu) == 5:
            _, offset, value = struct.unpack(">BHH", pdu)
            result.update({"address": 40001 + offset, "values": [value]})
        elif function == 16 and len(pdu) == 5:
            _, offset, quantity = struct.unpack(">BHH", pdu)
            result.update({"address": 40001 + offset, "quantity": quantity})
        return result

    def _state_snapshot(self) -> dict[str, object]:
        operation = self.transport.operation_snapshot()
        model_phase = getattr(self.transport.model.phase, "value", str(self.transport.model.phase))
        last_handshake = (
            dict(self.transport.handshake_history[-1])
            if self.transport.handshake_history
            else None
        )
        # Once a staged operation has completed, ``operation_snapshot`` is
        # idle again.  Preserve the last PLC handshake stage so the trace
        # still says ``completed`` instead of looking like no action ran.
        stage = str(operation.get("phase") or "")
        if not bool(operation.get("running")) and last_handshake:
            stage = str(last_handshake.get("phase") or stage)
        stage = stage or model_phase
        try:
            status = list(self.transport.read_holding_registers(40100, 17, unit_id=self.unit_id))
        except Exception:
            status = []
        return {
            "stage": stage,
            "plc_phase": model_phase,
            "operation": operation,
            "status_registers": {
                str(40100 + index): value for index, value in enumerate(status)
            },
            "last_handshake": last_handshake,
            "handshake_count": len(self.transport.handshake_history),
        }

    def _record_communication(
        self,
        *,
        transaction_id: int | None,
        protocol_id: int,
        unit_id: int,
        request: Mapping[str, object],
        response: Mapping[str, object] | None,
        error: Mapping[str, object] | None,
        started: float,
    ) -> None:
        with self._lock:
            state = self._state_snapshot()
            record: dict[str, object] = {
                "timestamp": time.time(),
                "transaction_id": transaction_id,
                "protocol_id": int(protocol_id),
                "unit_id": int(unit_id),
                "ok": error is None,
                "request": dict(request),
                "response": dict(response) if response is not None else None,
                "error": dict(error) if error is not None else None,
                "state": state,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            }
            self.communication_log.append(record)
            del self.communication_log[:-self.communication_history_limit]
            if self._trace_stream is not None:
                self._trace_stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._trace_stream.flush()
            sink = self.trace_sink
        # A sink may forward records to a UI that reads the in-memory log.
        # Invoke it after releasing the simulator lock so such a sink cannot
        # deadlock by calling ``get_communication_log``.
        if sink is not None:
            try:
                sink(record)
            except Exception:
                # Tracing is diagnostic and must never turn a successful PLC
                # request into a Modbus exception response.
                pass

    def get_communication_log(self, *, clear: bool = False) -> list[dict[str, object]]:
        """Return JSON-compatible Modbus traces for a UI or test harness."""

        with self._lock:
            records = [dict(record) for record in self.communication_log]
            if clear:
                self.communication_log.clear()
            return records

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
    parser.add_argument(
        "--log-file",
        type=pathlib.Path,
        default=None,
        help="append structured request/response records as JSONL",
    )
    parser.add_argument(
        "--log-history",
        type=int,
        default=2000,
        help="number of records retained in memory (default: 2000)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print each structured request/response record to stdout",
    )
    args = parser.parse_args(argv)

    def print_trace(record: Mapping[str, object]) -> None:
        print(json.dumps(record, ensure_ascii=False), flush=True)

    server = ModbusTcpSimulator(
        host=args.host,
        port=args.port,
        unit_id=args.unit_id,
        crucible_id=args.crucible_id,
        dosing_duration=args.dosing_duration,
        handshake_delay=args.handshake_delay,
        tick_interval=args.tick_interval,
        communication_history_limit=args.log_history,
        trace_file=args.log_file,
        trace_sink=print_trace if args.verbose else None,
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
