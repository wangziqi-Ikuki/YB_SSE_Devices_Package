"""Modbus TCP primitives used by the YB synthesis station.

The original Qt application addresses holding registers using the human
readable ``40001`` notation, while a Modbus PDU carries a zero based offset.
This module keeps that conversion in one place and does not depend on a
third-party Modbus package.  The standard-library client is deliberately
small: it implements the function codes used by the station (03, 06 and 16)
and can be replaced by another transport in tests or in a site deployment.

Business orchestration lives in :mod:`synthesis_direct`; this module only owns
transport, wire encoding and the YB register map.
"""

from __future__ import annotations

import math
import socket
import struct
import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Protocol, Sequence


class ModbusError(RuntimeError):
    """Base class for transport and protocol failures."""


class ModbusConnectionError(ModbusError):
    """The TCP connection could not be established or was lost."""


class ModbusTimeoutError(ModbusConnectionError):
    """A Modbus request did not receive a complete response in time."""


class ModbusProtocolError(ModbusError):
    """The peer returned an invalid Modbus TCP frame."""


class ModbusExceptionResponse(ModbusProtocolError):
    """The server returned a Modbus exception response."""

    def __init__(self, function: int, code: int) -> None:
        self.function = int(function)
        self.code = int(code)
        super().__init__(
            f"Modbus exception response: function={self.function}, code={self.code}"
        )


class ModbusTransport(Protocol):
    """Minimal transport contract consumed by the YB protocol layer.

    ``address`` accepts either a human holding-register address (for example
    ``40170``) or an already converted zero-based PDU offset.  The concrete
    transport uses ``base_address`` to disambiguate the two forms.
    """

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def read_holding_registers(
        self, address: int, count: int, *, unit_id: int = 1
    ) -> tuple[int, ...]: ...

    def write_holding_registers(
        self, address: int, values: Sequence[int], *, unit_id: int = 1
    ) -> None: ...

    def write_holding_register(
        self, address: int, value: int, *, unit_id: int = 1
    ) -> None: ...


def holding_register_offset(address: int, *, base_address: int = 40001) -> int:
    """Convert a 40001-style address to a zero-based Modbus offset.

    The Qt code writes ``40001 - REGISTER_START_ADDRESS`` and therefore uses
    40001 as offset zero.  Passing a value below ``base_address`` is supported
    for callers that already have a PDU offset; it is useful when consuming a
    vendor's raw register map.  Invalid or negative values are rejected.
    """

    if not isinstance(address, int) or isinstance(address, bool):
        raise TypeError("register address must be an integer")
    if not isinstance(base_address, int) or base_address < 0:
        raise ValueError("base_address must be a non-negative integer")
    offset = address - base_address if address >= base_address else address
    if offset < 0 or offset > 0xFFFF:
        raise ValueError(f"register address out of range: {address}")
    return offset


def _validate_unit_id(unit_id: int) -> int:
    if not isinstance(unit_id, int) or isinstance(unit_id, bool) or not 0 <= unit_id <= 247:
        raise ValueError("unit_id must be an integer in the range 0..247")
    return unit_id


def _validate_registers(values: Iterable[int]) -> tuple[int, ...]:
    result = tuple(values)
    if not result:
        raise ValueError("at least one register is required")
    if len(result) > 123:
        raise ValueError("a Modbus write may contain at most 123 registers")
    for value in result:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
            raise ValueError(f"register value out of range: {value!r}")
    return result


class ModbusTcpTransport:
    """Small synchronous Modbus TCP client using Python's socket module.

    It intentionally does not retry writes.  If a write times out, the caller
    cannot know whether the PLC applied it and must reconcile by reading the
    status registers before deciding whether to issue another command.
    """

    def __init__(
        self,
        host: str,
        port: int = 502,
        *,
        unit_id: int = 1,
        timeout: float = 1.0,
        base_address: int = 40001,
        socket_factory: object | None = None,
    ) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be in the range 1..65535")
        if timeout <= 0 or not math.isfinite(float(timeout)):
            raise ValueError("timeout must be a finite positive number")
        self.host = host
        self.port = port
        self.unit_id = _validate_unit_id(unit_id)
        self.timeout = float(timeout)
        self.base_address = int(base_address)
        self._socket_factory = socket_factory or socket.create_connection
        self._socket: socket.socket | None = None
        self._transaction_id = 0
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        with self._lock:
            if self._socket is not None:
                return
            try:
                sock = self._socket_factory((self.host, self.port), self.timeout)
                sock.settimeout(self.timeout)
            except socket.timeout as exc:
                raise ModbusTimeoutError(
                    f"timed out connecting to {self.host}:{self.port}"
                ) from exc
            except OSError as exc:
                raise ModbusConnectionError(
                    f"failed to connect to {self.host}:{self.port}: {exc}"
                ) from exc
            self._socket = sock

    def close(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def __enter__(self) -> "ModbusTcpTransport":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id % 0xFFFF) + 1
        return self._transaction_id

    def _recv_exact(self, size: int) -> bytes:
        sock = self._socket
        if sock is None:
            raise ModbusConnectionError("Modbus TCP transport is not connected")
        data = bytearray()
        while len(data) < size:
            try:
                chunk = sock.recv(size - len(data))
            except socket.timeout as exc:
                self.close()
                raise ModbusTimeoutError("timed out waiting for Modbus response") from exc
            except OSError as exc:
                self.close()
                raise ModbusConnectionError(f"Modbus receive failed: {exc}") from exc
            if not chunk:
                self.close()
                raise ModbusConnectionError("Modbus peer closed the connection")
            data.extend(chunk)
        return bytes(data)

    def _request(self, pdu: bytes, *, unit_id: int) -> bytes:
        unit_id = _validate_unit_id(unit_id)
        with self._lock:
            if self._socket is None:
                self.connect()
            sock = self._socket
            if sock is None:  # pragma: no cover - defensive for type checkers
                raise ModbusConnectionError("Modbus TCP transport is not connected")
            transaction_id = self._next_transaction_id()
            # MBAP length includes the unit identifier and PDU.
            frame = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, unit_id) + pdu
            try:
                sock.sendall(frame)
            except socket.timeout as exc:
                self.close()
                raise ModbusTimeoutError("timed out sending Modbus request") from exc
            except OSError as exc:
                self.close()
                raise ModbusConnectionError(f"Modbus send failed: {exc}") from exc

            header = self._recv_exact(7)
            response_tid, protocol_id, length, response_unit = struct.unpack(">HHHB", header)
            if response_tid != transaction_id:
                raise ModbusProtocolError(
                    f"transaction mismatch: expected {transaction_id}, got {response_tid}"
                )
            if protocol_id != 0:
                raise ModbusProtocolError(f"unsupported protocol id: {protocol_id}")
            if length < 2:
                raise ModbusProtocolError(f"invalid Modbus length: {length}")
            if response_unit != unit_id:
                raise ModbusProtocolError(
                    f"unit mismatch: expected {unit_id}, got {response_unit}"
                )
            response_pdu = self._recv_exact(length - 1)
            if not response_pdu:
                raise ModbusProtocolError("empty Modbus response PDU")
            function = response_pdu[0]
            if function & 0x80:
                if len(response_pdu) != 2:
                    raise ModbusProtocolError("malformed Modbus exception response")
                raise ModbusExceptionResponse(function & 0x7F, response_pdu[1])
            return response_pdu

    def read_holding_registers(
        self, address: int, count: int, *, unit_id: int | None = None
    ) -> tuple[int, ...]:
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 125:
            raise ValueError("count must be in the range 1..125")
        offset = holding_register_offset(address, base_address=self.base_address)
        request_pdu = struct.pack(">BHH", 3, offset, count)
        try:
            pdu = self._request(
                request_pdu,
                unit_id=self.unit_id if unit_id is None else unit_id,
            )
        except ModbusConnectionError:
            # Reads are idempotent, so reconnecting and retrying once is safe.
            # Writes intentionally remain single-attempt because a lost reply
            # cannot prove whether the PLC already applied the command.
            pdu = self._request(
                request_pdu,
                unit_id=self.unit_id if unit_id is None else unit_id,
            )
        if len(pdu) < 2 or pdu[0] != 3:
            raise ModbusProtocolError("unexpected function code in read response")
        byte_count = pdu[1]
        expected = count * 2
        if byte_count != expected or len(pdu) != expected + 2:
            raise ModbusProtocolError(
                f"invalid read byte count: expected {expected}, got {byte_count}"
            )
        return tuple(struct.unpack(f">{count}H", pdu[2:]))

    def write_holding_registers(
        self, address: int, values: Sequence[int], *, unit_id: int | None = None
    ) -> None:
        registers = _validate_registers(values)
        offset = holding_register_offset(address, base_address=self.base_address)
        pdu = struct.pack(">BHHB", 16, offset, len(registers), len(registers) * 2)
        pdu += struct.pack(f">{len(registers)}H", *registers)
        response = self._request(
            pdu, unit_id=self.unit_id if unit_id is None else unit_id
        )
        # Write-multiple response PDU is function + address(2) + quantity(2).
        if len(response) != 5 or response[0] != 16:
            raise ModbusProtocolError("unexpected function code in write response")
        response_offset, response_count = struct.unpack(">HH", response[1:])
        if response_offset != offset or response_count != len(registers):
            raise ModbusProtocolError("write response does not echo the request")

    def write_holding_register(
        self, address: int, value: int, *, unit_id: int | None = None
    ) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
            raise ValueError("register value must be an integer in the range 0..65535")
        offset = holding_register_offset(address, base_address=self.base_address)
        response = self._request(
            struct.pack(">BHH", 6, offset, value),
            unit_id=self.unit_id if unit_id is None else unit_id,
        )
        # Write-single response PDU is function + address(2) + value(2).
        if len(response) != 5 or response[0] != 6:
            raise ModbusProtocolError("unexpected function code in single-write response")
        response_offset, response_value = struct.unpack(">HH", response[1:])
        if response_offset != offset or response_value != value:
            raise ModbusProtocolError("single-write response does not echo the request")


class MemoryModbusTransport:
    """Deterministic in-memory transport for unit tests and dry-run adapters."""

    def __init__(self, *, size: int = 256, base_address: int = 40001) -> None:
        if not isinstance(size, int) or size <= 0:
            raise ValueError("size must be a positive integer")
        self.base_address = int(base_address)
        self.registers = [0] * size
        self.connected = False
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.on_write: object | None = None

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def _range(self, address: int, count: int) -> tuple[int, int]:
        offset = holding_register_offset(address, base_address=self.base_address)
        if count < 1 or offset + count > len(self.registers):
            raise ValueError("register range is outside the memory transport")
        return offset, offset + count

    def read_holding_registers(
        self, address: int, count: int, *, unit_id: int = 1
    ) -> tuple[int, ...]:
        _validate_unit_id(unit_id)
        if not self.connected:
            raise ModbusConnectionError("memory transport is not connected")
        start, end = self._range(address, count)
        return tuple(self.registers[start:end])

    def write_holding_registers(
        self, address: int, values: Sequence[int], *, unit_id: int = 1
    ) -> None:
        _validate_unit_id(unit_id)
        if not self.connected:
            raise ModbusConnectionError("memory transport is not connected")
        registers = _validate_registers(values)
        start, end = self._range(address, len(registers))
        self.registers[start:end] = registers
        self.writes.append((address, registers))
        callback = self.on_write
        if callable(callback):
            callback(address, registers)

    def write_holding_register(
        self, address: int, value: int, *, unit_id: int = 1
    ) -> None:
        self.write_holding_registers(address, (value,), unit_id=unit_id)


def encode_float32(value: float, *, word_order: str = "low_high") -> tuple[int, int]:
    """Encode an IEEE-754 float into two PLC registers.

    The YB Qt code stores the low 16-bit word first and the high word second.
    ``word_order='high_low'`` is provided for PLCs using the conventional
    opposite order.
    """

    number = float(value)
    if not math.isfinite(number):
        raise ValueError("float value must be finite")
    raw = struct.unpack(">I", struct.pack(">f", number))[0]
    high, low = (raw >> 16) & 0xFFFF, raw & 0xFFFF
    if word_order == "low_high":
        return low, high
    if word_order == "high_low":
        return high, low
    raise ValueError("word_order must be 'low_high' or 'high_low'")


def decode_float32(registers: Sequence[int], *, word_order: str = "low_high") -> float:
    if len(registers) != 2:
        raise ValueError("exactly two registers are required for a float")
    first, second = _validate_registers(registers)
    if word_order == "low_high":
        low, high = first, second
    elif word_order == "high_low":
        high, low = first, second
    else:
        raise ValueError("word_order must be 'low_high' or 'high_low'")
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def encode_ascii_registers(
    value: str, *, width: int = 20, byte_order: str = "low_high"
) -> tuple[int, ...]:
    """Encode an ASCII/UTF-8 string using the station's QR register layout.

    ``low_high`` matches the Qt reader: the first character is placed in the
    low byte and the second character in the high byte of each register.
    Values longer than ``width * 2`` bytes are rejected rather than silently
    truncated.
    """

    if not isinstance(value, str):
        raise TypeError("value must be a string")
    if not isinstance(width, int) or width <= 0:
        raise ValueError("width must be a positive integer")
    raw = value.encode("ascii")
    if len(raw) > width * 2:
        raise ValueError(f"value is longer than {width * 2} bytes")
    raw += b"\x00" * (width * 2 - len(raw))
    result: list[int] = []
    for index in range(0, len(raw), 2):
        first, second = raw[index], raw[index + 1]
        if byte_order == "low_high":
            result.append(first | (second << 8))
        elif byte_order == "high_low":
            result.append((first << 8) | second)
        else:
            raise ValueError("byte_order must be 'low_high' or 'high_low'")
    return tuple(result)


def decode_ascii_registers(
    registers: Sequence[int], *, byte_order: str = "low_high", strip: bool = True
) -> str:
    """Decode the QR string returned by registers 40170..40189."""

    values = _validate_registers(registers)
    raw = bytearray()
    for register in values:
        if byte_order == "low_high":
            raw.extend((register & 0xFF, (register >> 8) & 0xFF))
        elif byte_order == "high_low":
            raw.extend(((register >> 8) & 0xFF, register & 0xFF))
        else:
            raise ValueError("byte_order must be 'low_high' or 'high_low'")
    decoded = bytes(raw).split(b"\x00", 1)[0].decode("ascii")
    return decoded.strip() if strip else decoded


class SynthesisCommand(IntEnum):
    """Command values from the original PLC client."""

    DOWN_MATERIAL = 1
    UP_MATERIAL = 2
    SAMPLE = 3
    SEND_FIRING = 4
    FETCH_FIRING = 5
    FETCH_CUBIC = 7
    ACOUSTIC_RESONANCE = 8
    FETCH_ACOUSTIC_RESONANCE = 9


class CubicSource(IntEnum):
    """坩埚来源的设备包编号。

    上位机/PLC 的 command 7 使用 1 表示方舱、2 表示料架 2；设备包的
    高层动作使用从 0 开始的语义编号。两套编号必须在直连控制器边界转换，
    不能把设备包编号直接写入 PLC。
    """

    CABIN = 0
    RACK2 = 1


def cubic_source_to_plc(source: int) -> int:
    """Convert a package cubic-source number to the Qt/PLC wire value."""

    if isinstance(source, bool):
        raise ValueError("坩埚来源编号不能是布尔值")
    try:
        package_source = CubicSource(int(source))
    except (TypeError, ValueError) as exc:
        raise ValueError("坩埚来源必须是 0（方舱）或 1（料架2）") from exc
    return {
        CubicSource.CABIN: 1,
        CubicSource.RACK2: 2,
    }[package_source]


@dataclass(frozen=True)
class SynthesisRegisterMap:
    """Versioned addresses shared by the direct driver and PLC simulation."""

    base_address: int = 40001
    command_address: int = 40001
    status_address: int = 40100
    status_count: int = 17
    sampling_result_address: int = 40120
    sampling_result_count: int = 70
    qr_address: int = 40170
    qr_register_count: int = 20
    up_material_ready: int = 40090
    firing_ready: int = 40091
    close_cabin_door: int = 40096
    pause_command: int = 40098

    def status_register(self, index: int) -> int:
        if not 0 <= index < self.status_count:
            raise ValueError(f"status index out of range: {index}")
        return self.status_address + index


@dataclass(frozen=True)
class SynthesisStatusSnapshot:
    """Decoded status block returned by :class:`SynthesisModbusClient`."""

    down_material: int
    up_material: int
    sampling: int
    send_firing: int
    fetch_firing: int
    add_beads: int
    upper_pallet: int
    acoustic_resonance: int
    cabin_feed_state: int
    acoustic_process: int
    acoustic_fetch: int
    furnace: tuple[int, int, int, int]
    joule_heating: int
    pause: int

    @property
    def cabin_fetch_cubic(self) -> int:
        """Backward-compatible alias for the old, misleading field name.

        The PLC project calls register 40108 ``方舱进料状态``.  It is a cabin
        feed state, not the completion status of command 7; command 7 uses
        the upper-pallet task status at 40106.
        """

        return self.cabin_feed_state

    @classmethod
    def from_registers(cls, values: Sequence[int]) -> "SynthesisStatusSnapshot":
        if len(values) != 17:
            raise ValueError("the YB status block must contain 17 registers")
        return cls(
            down_material=int(values[0]),
            up_material=int(values[1]),
            sampling=int(values[2]),
            send_firing=int(values[3]),
            fetch_firing=int(values[4]),
            add_beads=int(values[5]),
            upper_pallet=int(values[6]),
            acoustic_resonance=int(values[7]),
            cabin_feed_state=int(values[8]),
            acoustic_process=int(values[9]),
            acoustic_fetch=int(values[15]),
            furnace=tuple(int(value) for value in values[10:14]),
            joule_heating=int(values[14]),
            pause=int(values[16]),
        )


@dataclass(frozen=True)
class SamplingResults:
    weights: tuple[float, ...]
    results: tuple[int, ...]
    qr_code: str


class SynthesisModbusClient:
    """Thin YB-specific façade over a :class:`ModbusTransport`.

    This class intentionally contains no workflow state.  It translates the
    common reads/writes used by higher-level actions and leaves task lifecycle
    and resource ownership to the device package and Uni-Lab OS.
    """

    def __init__(
        self,
        transport: ModbusTransport,
        *,
        register_map: SynthesisRegisterMap | None = None,
        unit_id: int = 1,
    ) -> None:
        self.transport = transport
        self.register_map = register_map or SynthesisRegisterMap()
        self.unit_id = _validate_unit_id(unit_id)

    def read_status(self) -> SynthesisStatusSnapshot:
        values = self.transport.read_holding_registers(
            self.register_map.status_address,
            self.register_map.status_count,
            unit_id=self.unit_id,
        )
        return SynthesisStatusSnapshot.from_registers(values)

    def read_sampling_results(self, material_count: int) -> SamplingResults:
        if not isinstance(material_count, int) or not 0 <= material_count <= 10:
            raise ValueError("material_count must be in the range 0..10")
        values = self.transport.read_holding_registers(
            self.register_map.sampling_result_address,
            self.register_map.sampling_result_count,
            unit_id=self.unit_id,
        )
        weights = tuple(
            decode_float32(values[index : index + 2])
            for index in range(0, material_count * 2, 2)
        )
        results = tuple(int(value) for value in values[30 : 30 + material_count])
        qr_code = decode_ascii_registers(values[50:70])
        return SamplingResults(weights=weights, results=results, qr_code=qr_code)

    def write_command(self, values: Sequence[int]) -> None:
        self.transport.write_holding_registers(
            self.register_map.command_address, values, unit_id=self.unit_id
        )

    def write_single_command(self, command: SynthesisCommand | int) -> None:
        self.transport.write_holding_register(
            self.register_map.command_address, int(command), unit_id=self.unit_id
        )

    def write_ready(self, address: int) -> None:
        self.transport.write_holding_register(address, 1, unit_id=self.unit_id)

    def write_close_cabin_door(self) -> None:
        self.write_ready(self.register_map.close_cabin_door)

    def write_pause(self, pause: bool) -> None:
        self.transport.write_holding_registers(
            self.register_map.pause_command,
            (100, 1 if pause else 2),
            unit_id=self.unit_id,
        )


def encode_sampling_command(
    *,
    slot: int,
    from_outside: bool,
    rack_positions: Sequence[int],
    masses: Sequence[float],
    tolerances: Sequence[float],
    cubic_type: int,
    bead_count: int,
    max_materials: int = 10,
) -> tuple[int, ...]:
    """Build the 81-register payload used by the Qt ``CMD_SAMPLE`` path."""

    if not isinstance(slot, int) or not 0 <= slot <= 0xFFFF:
        raise ValueError("slot must be an unsigned 16-bit integer")
    if not 0 <= len(rack_positions) <= max_materials:
        raise ValueError(f"at most {max_materials} materials are supported")
    if not (len(rack_positions) == len(masses) == len(tolerances)):
        raise ValueError("rack_positions, masses and tolerances must have equal length")
    if not isinstance(cubic_type, int) or not 0 <= cubic_type <= 0xFFFF:
        raise ValueError("cubic_type must be an unsigned 16-bit integer")
    if not isinstance(bead_count, int) or not 0 <= bead_count <= 0xFFFF:
        raise ValueError("bead_count must be an unsigned 16-bit integer")

    payload = [0] * 81
    payload[0] = int(SynthesisCommand.SAMPLE)
    payload[1] = 99 if from_outside else slot
    payload[2] = slot
    for index, (rack, mass, tolerance) in enumerate(
        zip(rack_positions, masses, tolerances)
    ):
        if not isinstance(rack, int) or not 0 <= rack <= 0xFFFF:
            raise ValueError("rack positions must be unsigned 16-bit integers")
        start = 3 + index * 5
        payload[start] = rack
        payload[start + 1 : start + 3] = encode_float32(mass)
        payload[start + 3 : start + 5] = encode_float32(tolerance)
    payload[79] = cubic_type
    payload[80] = bead_count
    return tuple(payload)


def _encode_position_pairs(
    command: SynthesisCommand,
    from_positions: Sequence[int],
    to_positions: Sequence[int],
    *,
    include_reserved_tail: bool,
    max_count: int = 10,
) -> tuple[int, ...]:
    if len(from_positions) != len(to_positions) or len(from_positions) > max_count:
        raise ValueError(
            f"from_positions 和 to_positions 必须等长且最多 {max_count} 项"
        )
    payload = [0] * (1 + max_count * 2 + (3 if include_reserved_tail else 0))
    payload[0] = int(command)
    for index, (source, destination) in enumerate(zip(from_positions, to_positions)):
        for value in (source, destination):
            if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
                raise ValueError("位置必须是无符号 16 位整数")
        payload[1 + index * 2] = source
        payload[2 + index * 2] = destination
    return tuple(payload)


def encode_down_material_command(
    from_positions: Sequence[int], to_positions: Sequence[int], *, max_count: int = 10
) -> tuple[int, ...]:
    """Encode Qt ``makeDownMaterialUnit`` (40001..40021)."""

    return _encode_position_pairs(
        SynthesisCommand.DOWN_MATERIAL,
        from_positions,
        to_positions,
        include_reserved_tail=False,
        max_count=max_count,
    )


def encode_up_material_command(
    from_positions: Sequence[int], to_positions: Sequence[int], *, max_count: int = 10
) -> tuple[int, ...]:
    """Encode Qt ``makeUpMaterialUnit`` (40001..40024)."""

    return _encode_position_pairs(
        SynthesisCommand.UP_MATERIAL,
        from_positions,
        to_positions,
        include_reserved_tail=True,
        max_count=max_count,
    )


def _pad_u16(values: Sequence[int], width: int) -> list[int]:
    if len(values) > width:
        raise ValueError(f"最多支持 {width} 个参数")
    result: list[int] = []
    for value in values:
        if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise ValueError("参数必须是无符号 16 位整数")
        result.append(value)
    result.extend([0] * (width - len(result)))
    return result


def encode_send_firing_command(
    *,
    position: int,
    temperatures: Sequence[int] = (),
    times: Sequence[int] = (),
    joule_position: int = 0,
    joule_mode: int = 0,
    joule_carrier_type: int = 0,
    joule_constant_temperature: int = 0,
    joule_constant_hold_time: int = 0,
    joule_slope_temperatures: Sequence[int] = (),
    joule_slope_speeds: Sequence[int] = (),
    joule_slope_hold_times: Sequence[int] = (),
) -> tuple[int, ...]:
    """Encode Qt ``makeSendFiringUnit`` (40001..40081).

    ``position`` 1..4 selects a furnace; 5 selects Joule heating.  The carrier
    type follows the Qt input convention (0-based) and is written as +1.
    """

    if position not in {1, 2, 3, 4, 5}:
        raise ValueError("position 必须是 1 到 5")
    payload = [0] * 81
    payload[0] = int(SynthesisCommand.SEND_FIRING)
    if position < 5:
        temps = _pad_u16(temperatures, 6)
        holds = _pad_u16(times, 6)
        start = 1 + (position - 1) * 15
        payload[start] = 1
        payload[start + 1 : start + 7] = temps
        payload[start + 7 : start + 13] = holds
        return tuple(payload)
    values = [joule_position, joule_mode, joule_carrier_type + 1,
              joule_constant_temperature, joule_constant_hold_time]
    if not isinstance(joule_carrier_type, int) or not 0 <= joule_carrier_type <= 0xFFFE:
        raise ValueError("焦耳热载具类型必须是 0 到 65534")
    for value in values:
        if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise ValueError("焦耳热参数必须是无符号 16 位整数")
    payload[61:66] = values
    payload[66:71] = _pad_u16(joule_slope_temperatures, 5)
    payload[71:76] = _pad_u16(joule_slope_speeds, 5)
    payload[76:81] = _pad_u16(joule_slope_hold_times, 5)
    return tuple(payload)


def encode_fetch_firing_command(
    *, position: int, joule_position: int = 0, joule_carrier_type: int = 0
) -> tuple[int, ...]:
    """Encode Qt ``makeFetchFiringUnit`` (40001..40011)."""

    if position not in {1, 2, 3, 4, 5}:
        raise ValueError("position 必须是 1 到 5")
    payload = [0] * 11
    payload[0] = int(SynthesisCommand.FETCH_FIRING)
    if position < 5:
        payload[1] = position
        payload[2] = position
    else:
        if not 0 <= joule_carrier_type <= 0xFFFF or not 0 <= joule_position <= 0xFFFF:
            raise ValueError("焦耳热参数必须是无符号 16 位整数")
        payload[9] = joule_carrier_type + 1
        payload[10] = joule_position
    return tuple(payload)


def encode_fetch_cubic_command(
    *,
    source: int,
    destination: int,
    pallet_type: int,
    slot_numbers: Sequence[int] = (),
    bead_source: int = 0,
) -> tuple[int, ...]:
    """Encode Qt ``makeFetchCubicUnit`` (40001..40011)."""

    payload = [0] * 11
    payload[0] = int(SynthesisCommand.FETCH_CUBIC)
    payload[1:4] = _pad_u16((source, destination, pallet_type), 3)
    payload[4:10] = _pad_u16(slot_numbers, 6)
    payload[10] = int(bead_source)
    if not 0 <= payload[10] <= 0xFFFF:
        raise ValueError("bead_source 必须是无符号 16 位整数")
    return tuple(payload)


def encode_add_bead_command(*, source: int) -> tuple[int, ...]:
    """Encode the Qt add-bead path, which reuses command 7."""

    return encode_fetch_cubic_command(
        source=source,
        destination=1,
        pallet_type=0,
        slot_numbers=(),
        bead_source=source,
    )


def encode_acoustic_resonance_command(
    *,
    fetch_position: int,
    accelerations: Sequence[int] = (),
    frequencies: Sequence[int] = (),
    times: Sequence[int] = (),
) -> tuple[int, ...]:
    """Encode Qt ``makeAcousticResonanceUnit`` (40001..40021)."""

    if not isinstance(fetch_position, int) or not 0 <= fetch_position <= 0xFFFF:
        raise ValueError("fetch_position 必须是无符号 16 位整数")
    if not (len(accelerations) == len(frequencies) == len(times)):
        raise ValueError("accelerations、frequencies、times 必须等长")
    if len(accelerations) > 6:
        raise ValueError("声共振最多支持 6 组参数")
    payload = [0] * 21
    payload[0] = int(SynthesisCommand.ACOUSTIC_RESONANCE)
    payload[1] = int(fetch_position)
    payload[2] = 2
    for index, group in enumerate(zip(accelerations, frequencies, times)):
        payload[3 + index * 3 : 6 + index * 3] = _pad_u16(group, 3)
    return tuple(payload)


def encode_fetch_acoustic_resonance_command() -> tuple[int, ...]:
    """Encode Qt ``makeFetchAcousticResonanceUnit`` (40001..40003)."""

    return int(SynthesisCommand.FETCH_ACOUSTIC_RESONANCE), 1, 1


__all__ = [
    "MemoryModbusTransport",
    "ModbusConnectionError",
    "ModbusError",
    "ModbusExceptionResponse",
    "ModbusProtocolError",
    "ModbusTcpTransport",
    "ModbusTimeoutError",
    "ModbusTransport",
    "CubicSource",
    "SamplingResults",
    "SynthesisCommand",
    "SynthesisModbusClient",
    "SynthesisRegisterMap",
    "SynthesisStatusSnapshot",
    "decode_ascii_registers",
    "decode_float32",
    "cubic_source_to_plc",
    "encode_ascii_registers",
    "encode_acoustic_resonance_command",
    "encode_add_bead_command",
    "encode_down_material_command",
    "encode_float32",
    "encode_fetch_acoustic_resonance_command",
    "encode_fetch_cubic_command",
    "encode_fetch_firing_command",
    "encode_sampling_command",
    "encode_send_firing_command",
    "encode_up_material_command",
    "holding_register_offset",
]
