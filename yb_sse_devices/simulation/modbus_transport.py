"""Package-local Modbus transport backed by the deterministic PLC model.

The production adapter speaks Modbus TCP.  This transport deliberately keeps
the same :class:`~yb_sse_devices.synthesis_modbus.ModbusTransport` seam while
mapping a small, documented subset of command 3 to the in-process PLC model.
That gives the device package a real protocol loop in dry-run mode without
starting a second TCP/JSON server or hiding the internal scan interlock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from yb_sse_devices.simulation.plc_model import (
    SamplingPhase,
    SimulationError,
    SynthesisPlcModel,
)
from yb_sse_devices.synthesis_modbus import (
    ModbusConnectionError,
    SynthesisCommand,
    SynthesisRegisterMap,
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
        handshake_delay: float = 0.1,
        failure_plan: Mapping[str, str] | None = None,
        handshake_history_limit: int = 100,
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
        if float(handshake_delay) < 0:
            raise ValueError("handshake_delay must be non-negative")
        if int(handshake_history_limit) < 1:
            raise ValueError("handshake_history_limit must be positive")
        self.handshake_delay = float(handshake_delay)
        self.failure_plan = {
            str(command): str(message)
            for command, message in (failure_plan or {}).items()
        }
        self.handshake_history_limit = int(handshake_history_limit)
        self.handshake_history: list[dict[str, object]] = []
        self.connected = False
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.last_command: tuple[int, ...] = ()
        self.last_task_id = ""
        self.execution_task_id = ""
        self.expected_crucible_id = ""
        self._task_sequence = 0
        self._registers: dict[int, int] = {}
        self._pending_sampling: tuple[str, int, dict[str, float]] | None = None
        # Non-sampling PLC operations use the same status block as the field
        # controller.  The dry-run marks them RUNNING on write and COMPLETED
        # after a short deterministic interval; this exercises the handshake
        # without pretending to model robot motion or furnace physics.
        self._status_registers: dict[int, int] = {}
        self._pending_operations: list[tuple[int, float]] = []
        self._status_map = {
            int(SynthesisCommand.DOWN_MATERIAL): 0,
            int(SynthesisCommand.UP_MATERIAL): 1,
            int(SynthesisCommand.SEND_FIRING): 3,
            int(SynthesisCommand.FETCH_FIRING): 4,
            int(SynthesisCommand.FETCH_CUBIC): 8,
            int(SynthesisCommand.ACOUSTIC_RESONANCE): 7,
            int(SynthesisCommand.FETCH_ACOUSTIC_RESONANCE): 15,
        }

    @property
    def paused(self) -> bool:
        return bool(self._status_registers.get(40116, 0) == 1)

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def reset(self) -> None:
        """Reset the simulated PLC and all pending command state."""

        self.model.reset()
        self._status_registers.clear()
        self._pending_operations.clear()
        self._pending_sampling = None
        self.last_task_id = ""
        self.execution_task_id = ""
        self.expected_crucible_id = ""
        self.handshake_history.clear()

    def clear_fault(self, name: str | None = None) -> None:
        self.model.clear_fault(name)
        for register, value in list(self._status_registers.items()):
            if value == 3:
                self._status_registers[register] = 0

    def _record_handshake(self, command: str, phase: str, **payload: object) -> None:
        self.handshake_history.append(
            {"command": command, "phase": phase, "task_id": self.last_task_id, **payload}
        )
        del self.handshake_history[:-self.handshake_history_limit]

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
        start = int(address)
        values = self.model.holding_registers(start=start, count=count)
        # Overlay the simulated handshake bits on any read that intersects
        # the 40100..40116 status block (not only a full-block read).
        for index in range(count):
            register = start + index
            if 40100 <= register <= 40116:
                values[index] = self._status_registers.get(register, values[index])
                if register == 40102:
                    # The sampling bit is owned by the state model, including
                    # its scan/bind interlock and fault state.
                    values[index] = self.model.sampling_status
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
        if address == 40001:
            if payload[0] == int(SynthesisCommand.SAMPLE):
                self._apply_sampling_command(payload)
            elif payload[0] in self._status_map:
                # Command 7 is shared by fetch-cubic and add-bead in the Qt
                # client.  The add-bead encoder carries pallet_type=0.
                if (
                    payload[0] == int(SynthesisCommand.FETCH_CUBIC)
                    and len(payload) >= 11
                    and payload[3] == 0
                ):
                    self._start_operation(payload[0], status_index=5)
                elif payload[0] == int(SynthesisCommand.SEND_FIRING):
                    self._start_firing_operation(payload)
                else:
                    self._start_operation(payload[0])
        elif address == 40098 and len(payload) >= 2 and payload[0] == 100:
            # 40098/40099 handshake: 1 pause, 2 resume.
            self._status_registers[40116] = 1 if payload[1] == 1 else 0

    def write_holding_register(
        self, address: int, value: int, *, unit_id: int = 1
    ) -> None:
        self.write_holding_registers(address, (value,), unit_id=unit_id)

    def advance(self, seconds: float) -> None:
        """Advance deterministic PLC time and expose any resulting state."""

        value = float(seconds)
        if value < 0:
            raise ValueError("seconds must be non-negative")
        if self.paused:
            return
        previous_phase = self.model.phase
        self.model.advance(value)
        if previous_phase is not SamplingPhase.COMPLETED and self.model.phase is SamplingPhase.COMPLETED:
            self._record_handshake("sample_add_powder_and_beads", "completed")
        elif previous_phase is not SamplingPhase.FAULT and self.model.phase is SamplingPhase.FAULT:
            fault = self.model.fault
            self._record_handshake(
                "sample_add_powder_and_beads",
                "failed",
                message=fault.message if fault else "仿真工艺失败",
            )
        remaining: list[tuple[int, float]] = []
        for status_register, due_in in self._pending_operations:
            due = due_in - value
            if due <= 0:
                self._status_registers[status_register] = 2
                self._record_handshake(
                    f"status_{status_register}", "completed", status_register=status_register
                )
            else:
                remaining.append((status_register, due))
        self._pending_operations = remaining

    def _start_operation(self, command: int, *, status_index: int | None = None) -> None:
        if self._pending_operations or self.model.phase in {
            SamplingPhase.SCANNING,
            SamplingPhase.BOUND,
            SamplingPhase.DOSING,
        }:
            raise SimulationError("上一条 PLC 动作尚未完成，设备动作互斥")
        index = self._status_map.get(int(command)) if status_index is None else status_index
        if index is None:
            return
        command_name = SynthesisCommand(int(command)).name.lower()
        self._record_handshake(command_name, "parameters_written")
        self._record_handshake(command_name, "accepted")
        register = SynthesisRegisterMap().status_register(index)
        failure = self.failure_plan.get(command_name)
        if failure:
            self._status_registers[register] = 3
            self._record_handshake(command_name, "failed", message=failure)
            return
        self._status_registers[register] = 1
        self._record_handshake(command_name, "running")
        self._pending_operations.append((register, self.handshake_delay))

    def _start_firing_operation(self, payload: tuple[int, ...]) -> None:
        """Reflect furnace/Joule heating handshake in the status block."""
        command_name = SynthesisCommand.SEND_FIRING.name.lower()
        if self._pending_operations:
            raise SimulationError("上一条 PLC 动作尚未完成，设备动作互斥")
        self._record_handshake(command_name, "parameters_written")
        self._record_handshake(command_name, "accepted")
        command_register = SynthesisRegisterMap().status_register(3)
        failure = self.failure_plan.get(command_name)
        if failure:
            self._status_registers[command_register] = 3
            self._record_handshake(command_name, "failed", message=failure)
            return
        self._status_registers[command_register] = 1
        self._record_handshake(command_name, "running")
        self._pending_operations.append((command_register, self.handshake_delay))
        position = 0
        for index in range(4):
            marker = 1 + index * 15
            if len(payload) > marker and payload[marker] == 1:
                position = index + 1
                break
        status_index = 14 if position == 0 else 9 + position
        register = SynthesisRegisterMap().status_register(status_index)
        self._status_registers[register] = 1
        self._pending_operations.append((register, self.handshake_delay))

    def bind_crucible(self, crucible_id: str | None = None) -> None:
        """Complete a manual scanner response in a non-auto-scan simulation."""

        self.model.bind_crucible(crucible_id or self.crucible_id)
        self._start_pending_sampling()

    def _apply_sampling_command(self, payload: tuple[int, ...]) -> None:
        if len(payload) < 81:
            raise ValueError("CMD_SAMPLE payload must contain 81 registers")
        if self._pending_operations:
            raise SimulationError("上一条 PLC 动作尚未完成，设备动作互斥")
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
        self.execution_task_id = task_id
        self.last_task_id = task_id
        expected_id = self.expected_crucible_id or self.crucible_id
        self._record_handshake("sample_add_powder_and_beads", "parameters_written")
        self._record_handshake("sample_add_powder_and_beads", "accepted")
        failure = self.failure_plan.get("sample_add_powder_and_beads")
        if failure:
            self.model.inject_fault(
                "scanner",
                code="SIM_HANDSHAKE_FAILURE",
                message=failure,
                once=True,
            )
        self.model.begin_crucible_binding(
            task_id,
            slot,
            expected_crucible_id=expected_id,
            cubic_type=cubic_type,
        )
        if self.model.phase is SamplingPhase.FAULT:
            fault = self.model.fault
            self._record_handshake(
                "sample_add_powder_and_beads",
                "failed",
                message=fault.message if fault else "仿真握手失败",
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
