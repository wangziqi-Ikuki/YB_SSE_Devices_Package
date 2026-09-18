"""Package-local Modbus transport backed by the deterministic PLC model.

The production adapter speaks Modbus TCP.  This transport deliberately keeps
the same :class:`~yb_sse_devices.synthesis_modbus.ModbusTransport` seam while
mapping a small, documented subset of command 3 to the in-process PLC model.
That gives the device package a real protocol loop in dry-run mode without
starting a second TCP/JSON server or hiding the internal scan interlock.
"""

from __future__ import annotations

from collections.abc import Sequence

from yb_sse_devices.simulation.plc_model import SamplingPhase, SynthesisPlcModel
from yb_sse_devices.synthesis_modbus import (
    ModbusConnectionError,
    SynthesisCommand,
    decode_float32,
)


class SynthesisSimulationTransport:
    """A deterministic Modbus-like transport for package-local simulation.

    ``write_holding_registers(40001, payload)`` accepts the same 81-register
    payload emitted by the Qt client.  The payload does not contain a task ID
    or an expected QR code, so those are supplied as simulation configuration.
    The model still requires the internal scan to complete before dosing can
    start; ``auto_scan_id`` only controls how the simulated scanner answers.
    """

    def __init__(
        self,
        model: SynthesisPlcModel | None = None,
        *,
        task_id_prefix: str = "SIM-TASK",
        crucible_id: str = "CRU-SIM-001",
        material_names: Sequence[str] | None = None,
        base_address: int = 40001,
    ) -> None:
        self.model = model or SynthesisPlcModel(auto_scan_id=crucible_id)
        self.task_id_prefix = str(task_id_prefix).strip() or "SIM-TASK"
        self.crucible_id = str(crucible_id).strip()
        if not self.crucible_id:
            raise ValueError("crucible_id 不能为空")
        self.material_names = tuple(str(name).strip() for name in (material_names or ()))
        if any(not name for name in self.material_names):
            raise ValueError("material_names 不能包含空名称")
        self.base_address = int(base_address)
        self.connected = False
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.last_command: tuple[int, ...] = ()
        self.last_task_id = ""
        self._task_sequence = 0
        self._registers: dict[int, int] = {}
        self._pending_sampling: tuple[str, int, dict[str, float]] | None = None

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def _require_connected(self) -> None:
        if not self.connected:
            raise ModbusConnectionError("simulation transport is not connected")

    def read_holding_registers(
        self, address: int, count: int, *, unit_id: int = 1
    ) -> tuple[int, ...]:
        del unit_id  # Unit 1 is the only simulated slave.
        self._require_connected()
        if not isinstance(count, int) or count < 1:
            raise ValueError("count must be a positive integer")
        # The model is authoritative for the status/result ranges.  For other
        # registers retain values written by a test or by a control action.
        values = self.model.holding_registers(start=int(address), count=count)
        start = int(address)
        for index in range(count):
            if start + index not in {
                self.model.SAMPLE_STATUS_REGISTER,
                *range(self.model.WEIGHT_START_REGISTER, self.model.QR_START_REGISTER + self.model.QR_REGISTER_COUNT),
            }:
                values[index] = self._registers.get(start + index, values[index])
        return tuple(values)

    def write_holding_registers(
        self, address: int, values: Sequence[int], *, unit_id: int = 1
    ) -> None:
        del unit_id
        self._require_connected()
        payload = tuple(int(value) & 0xFFFF for value in values)
        if not payload:
            raise ValueError("at least one register is required")
        address = int(address)
        self.writes.append((address, payload))
        self.last_command = payload if address == 40001 else self.last_command
        for offset, value in enumerate(payload):
            self._registers[address + offset] = value
        if address == 40001 and payload[0] == int(SynthesisCommand.SAMPLE):
            self._apply_sampling_command(payload)

    def write_holding_register(
        self, address: int, value: int, *, unit_id: int = 1
    ) -> None:
        self.write_holding_registers(address, (value,), unit_id=unit_id)

    def advance(self, seconds: float) -> None:
        """Advance deterministic PLC time and expose any resulting state."""

        self.model.advance(seconds)

    def bind_crucible(self, crucible_id: str | None = None) -> None:
        """Complete a manual scanner response in a non-auto-scan simulation."""

        self.model.bind_crucible(crucible_id or self.crucible_id)
        self._start_pending_sampling()

    def _apply_sampling_command(self, payload: tuple[int, ...]) -> None:
        if len(payload) < 81:
            raise ValueError("CMD_SAMPLE payload must contain 81 registers")
        slot = int(payload[2])
        if slot <= 0:
            raise ValueError("CMD_SAMPLE slot must be positive")
        cubic_type = int(payload[79])
        names = self.material_names
        targets: dict[str, float] = {}
        for index in range(10):
            start = 3 + index * 5
            rack_position = int(payload[start])
            if rack_position == 0:
                continue
            weight = decode_float32(payload[start + 1 : start + 3])
            if weight <= 0:
                continue
            name = names[index] if index < len(names) else f"material_{index + 1}"
            targets[name] = weight
        if not targets:
            raise ValueError("CMD_SAMPLE 至少需要一组正重量物料")
        self._task_sequence += 1
        task_id = f"{self.task_id_prefix}-{self._task_sequence:04d}"
        self.last_task_id = task_id
        self.model.begin_crucible_binding(
            task_id,
            slot,
            expected_crucible_id=self.crucible_id,
            cubic_type=cubic_type,
        )
        # auto_scan_id is an explicit simulation choice.  If disabled, the
        # caller must invoke bind_crucible() before dosing can begin.
        if self.model.auto_scan_id:
            self.model.advance(0)
        self._pending_sampling = (task_id, slot, targets)
        self._start_pending_sampling()

    def _start_pending_sampling(self) -> None:
        pending = self._pending_sampling
        if pending is None or self.model.phase is not SamplingPhase.BOUND:
            return
        task_id, slot, targets = pending
        self.model.start_sampling(
            task_id,
            slot,
            targets,
            expected_results={name: 1 for name in targets},
        )
        self._pending_sampling = None


__all__ = ["SynthesisSimulationTransport"]
