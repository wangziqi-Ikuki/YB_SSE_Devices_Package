"""Package-local Modbus transport backed by the deterministic PLC model.

The production adapter speaks Modbus TCP.  This transport deliberately keeps
the same :class:`~yb_sse_devices.synthesis_modbus.ModbusTransport` seam while
mapping the Qt command set to the in-process PLC model.  Sampling uses the
explicit scan/dosing model; the other PLC flows use staged robot/device
handshakes and the confirmed 40100--40116 status block.  This gives the
device package a real protocol loop in dry-run mode without starting a second
TCP/JSON server or hiding the internal scan interlock.
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
        supplied_model = model
        self.model = model or SynthesisPlcModel(auto_scan_id=crucible_id)
        # An explicitly supplied model with ``auto_scan_id=None`` represents
        # a real/manual scanner path.  A transport-created model gets the
        # deterministic scanner answer used by the default dry-run.
        self._auto_scan_id = (
            str(self.model.auto_scan_id).strip()
            if supplied_model is not None and self.model.auto_scan_id
            else (str(crucible_id).strip() if supplied_model is None else None)
        )
        # The transport owns the PLC-side scan handshake delay.  Keep the
        # lower-level model explicit so a command write does not jump from
        # "scan requested" straight to "bound" in the same call stack.
        self.model.auto_scan_id = None
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
        self._pending_scan: float | None = None
        # Non-sampling PLC operations use the same status block as the field
        # controller.  The dry-run marks them RUNNING on write and COMPLETED
        # after a short deterministic interval; this exercises the handshake
        # without pretending to model robot motion or furnace physics.
        self._status_registers: dict[int, int] = {}
        self._active_operation: dict[str, object] | None = None
        # These are the PLC-side gates confirmed by SBR_105/SBR_106 and the
        # station flow blocks.  They default to the ready state so a dry-run
        # can start immediately, but tests and the desktop simulator can turn
        # them off to exercise the same rejection paths as the PLC.
        self.interlocks: dict[str, bool] = {
            "initialized": True,
            "emergency_stop": False,
            "doors_closed": True,
            "robot_ready": True,
            "robot_auto": True,
            "robot_safe": True,
            "servo_ready": True,
            "communications_ok": True,
            "scanner_ready": True,
            "furnace_ready": True,
            "furnace_open_allowed": True,
            "resonance_ready": True,
        }
        self._status_map = {
            int(SynthesisCommand.DOWN_MATERIAL): 0,
            int(SynthesisCommand.UP_MATERIAL): 1,
            int(SynthesisCommand.SEND_FIRING): 3,
            int(SynthesisCommand.FETCH_FIRING): 4,
            # PLC 40106 is the upper-pallet task.  40108 is only the cabin
            # feed/door state and is not the completion register for command 7.
            int(SynthesisCommand.FETCH_CUBIC): 6,
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
        self._active_operation = None
        self._pending_scan = None
        self.interlocks.update(
            {
                "initialized": True,
                "emergency_stop": False,
                "doors_closed": True,
                "robot_ready": True,
                "robot_auto": True,
                "robot_safe": True,
                "servo_ready": True,
                "communications_ok": True,
                "scanner_ready": True,
                "furnace_ready": True,
                "furnace_open_allowed": True,
                "resonance_ready": True,
            }
        )
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

    def set_interlock(self, name: str, allowed: bool) -> None:
        """Set one PLC safety/readiness condition in the local simulator."""

        key = str(name).strip()
        if key not in self.interlocks:
            raise ValueError(f"未知 PLC 互锁条件: {key}")
        self.interlocks[key] = bool(allowed)

    def interlock_snapshot(self) -> dict[str, bool]:
        return dict(self.interlocks)

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
                    self._start_operation(
                        payload[0], status_index=5, command_name_override="add_bead"
                    )
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
        # Consume the PLC robot-to-scanner handshake before advancing the
        # dosing timer.  A positive handshake delay makes SCANNING visible to
        # a poller instead of completing the scan in the command-write call.
        remaining_time = value
        if self._pending_scan is not None:
            self._pending_scan -= remaining_time
            if self._pending_scan <= 0:
                # Preserve the established simulator contract: the caller's
                # elapsed tick is also applied to the dosing step after the
                # scan handshake.  This keeps ``advance(1.0)`` compatible
                # with the original deterministic tests while still exposing
                # the intermediate SCANNING state to pollers.
                remaining_time = value
                self._pending_scan = None
                try:
                    self.model.bind_crucible(
                        self.expected_crucible_id or self.crucible_id
                    )
                    self._start_pending_sampling()
                except SimulationError as exc:
                    self._record_handshake(
                        "sample_add_powder_and_beads", "failed", message=str(exc)
                    )
            else:
                remaining_time = 0.0
        previous_phase = self.model.phase
        self.model.advance(remaining_time)
        if previous_phase is not SamplingPhase.COMPLETED and self.model.phase is SamplingPhase.COMPLETED:
            self._record_handshake("sample_add_powder_and_beads", "completed")
        elif previous_phase is not SamplingPhase.FAULT and self.model.phase is SamplingPhase.FAULT:
            fault = self.model.fault
            self._record_handshake(
                "sample_add_powder_and_beads",
                "failed",
                message=fault.message if fault else "仿真工艺失败",
            )
        self._advance_active_operation(value)

    def _advance_active_operation(self, seconds: float) -> None:
        """Advance the staged PLC flow used by non-sampling commands."""

        operation = self._active_operation
        if operation is None:
            return
        remaining = float(seconds)
        phases = operation["phases"]
        if not isinstance(phases, list):  # pragma: no cover - defensive
            return
        while operation is self._active_operation:
            phase_index = int(operation["phase_index"])
            phase_remaining = float(operation["phase_remaining"])
            if remaining < phase_remaining and phase_remaining > 0:
                operation["phase_remaining"] = phase_remaining - remaining
                return
            remaining = max(0.0, remaining - phase_remaining)
            phase_name = str(phases[phase_index])
            self._record_handshake(
                str(operation["command"]),
                "phase_completed",
                stage=phase_name,
            )
            next_index = phase_index + 1
            if next_index >= len(phases):
                for register in operation["status_registers"]:
                    self._status_registers[int(register)] = 2
                self._record_handshake(
                    str(operation["command"]),
                    "completed",
                    status_registers=list(operation["status_registers"]),
                )
                self._active_operation = None
                return
            operation["phase_index"] = next_index
            operation["phase_remaining"] = float(operation["phase_durations"][next_index])
            self._record_handshake(
                str(operation["command"]),
                "phase_started",
                stage=str(phases[next_index]),
            )
            if remaining <= 0 and operation["phase_remaining"] > 0:
                return

    def operation_snapshot(self) -> dict[str, object]:
        """Return the current staged PLC operation for the local simulator."""

        operation = self._active_operation
        if operation is None:
            return {"command": "", "phase": "idle", "running": False}
        phases = operation["phases"]
        index = int(operation["phase_index"])
        return {
            "command": str(operation["command"]),
            "phase": str(phases[index]) if isinstance(phases, list) else "running",
            "phase_index": index,
            "running": True,
        }

    def _start_operation(
        self,
        command: int,
        *,
        status_index: int | None = None,
        command_name_override: str | None = None,
    ) -> None:
        if self._active_operation is not None or self.model.phase in {
            SamplingPhase.SCANNING,
            SamplingPhase.BOUND,
            SamplingPhase.DOSING,
        }:
            raise SimulationError("上一条 PLC 动作尚未完成，设备动作互斥")
        index = self._status_map.get(int(command)) if status_index is None else status_index
        if index is None:
            return
        command_name = command_name_override or SynthesisCommand(int(command)).name.lower()
        return self._start_staged_operation(
            command_name,
            index,
            status_index=status_index,
        )

    def _start_staged_operation(
        self,
        command_name: str,
        index: int,
        *,
        status_index: int | None = None,
        phase_names: Sequence[str] | None = None,
    ) -> None:
        """Start a PLC flow with explicit robot/device phases."""

        failure = self._interlock_failure(command_name)
        register = SynthesisRegisterMap().status_register(index)
        if failure:
            self._status_registers[register] = 3
            self._record_handshake(command_name, "failed", message=failure)
            return
        if self._active_operation is not None:
            raise SimulationError("上一条 PLC 动作尚未完成，设备动作互斥")
        self._record_handshake(command_name, "parameters_written")
        self._record_handshake(command_name, "accepted")
        failure = self.failure_plan.get(command_name)
        if failure:
            self._status_registers[register] = 3
            self._record_handshake(command_name, "failed", message=failure)
            return
        self._status_registers[register] = 1
        self._record_handshake(command_name, "running")
        status_registers = [register]
        if command_name == SynthesisCommand.ACOUSTIC_RESONANCE.name.lower():
            # 40107 is the loading task; 40109 is the acoustic process state.
            process_register = SynthesisRegisterMap().status_register(9)
            self._status_registers[process_register] = 1
            status_registers.append(process_register)
        phases = list(phase_names or self._default_phases(command_name))
        if not phases:
            phases = ["device_action"]
        duration = self.handshake_delay / len(phases)
        self._active_operation = {
            "command": command_name,
            "status_registers": status_registers,
            "phases": phases,
            "phase_durations": [duration] * len(phases),
            "phase_index": 0,
            "phase_remaining": duration,
        }
        self._record_handshake(command_name, "phase_started", stage=phases[0])

    @staticmethod
    def _default_phases(command_name: str) -> list[str]:
        return {
            "down_material": ["robot_permission", "robot_motion", "position_update"],
            "up_material": ["robot_permission", "robot_motion", "position_update"],
            "fetch_firing": ["temperature_check", "furnace_door_open", "robot_unload"],
            "fetch_cubic": ["cabin_transition", "robot_pick", "pallet_place"],
            "add_bead": ["bead_mechanism_ready", "robot_pick", "bead_load"],
            "acoustic_resonance": [
                "resonance_door_open",
                "recipe_selected",
                "resonance_process",
            ],
            "fetch_acoustic_resonance": ["resonance_reset", "robot_unload"],
            "send_firing": ["recipe_written", "load_permission", "furnace_process"],
        }.get(command_name, ["device_action"])

    def _start_firing_operation(self, payload: tuple[int, ...]) -> None:
        """Reflect furnace/Joule heating handshake in the status block."""
        command_name = SynthesisCommand.SEND_FIRING.name.lower()
        if self._active_operation is not None:
            raise SimulationError("上一条 PLC 动作尚未完成，设备动作互斥")
        failure = self._interlock_failure(command_name)
        command_register = SynthesisRegisterMap().status_register(3)
        if failure:
            self._status_registers[command_register] = 3
            self._record_handshake(command_name, "failed", message=failure)
            return
        self._record_handshake(command_name, "parameters_written")
        self._record_handshake(command_name, "accepted")
        failure = self.failure_plan.get(command_name)
        if failure:
            self._status_registers[command_register] = 3
            self._record_handshake(command_name, "failed", message=failure)
            return
        self._status_registers[command_register] = 1
        self._record_handshake(command_name, "running")
        position = 0
        for index in range(4):
            marker = 1 + index * 15
            if len(payload) > marker and payload[marker] == 1:
                position = index + 1
                break
        status_index = 14 if position == 0 else 9 + position
        register = SynthesisRegisterMap().status_register(status_index)
        self._status_registers[register] = 1
        phases = (
            ["joule_recipe_written", "joule_load_permission", "joule_process"]
            if position == 0
            else ["recipe_written", "furnace_load_permission", "furnace_process"]
        )
        duration = self.handshake_delay / len(phases)
        self._active_operation = {
            "command": command_name,
            "status_registers": [command_register, register],
            "phases": phases,
            "phase_durations": [duration] * len(phases),
            "phase_index": 0,
            "phase_remaining": duration,
        }
        self._record_handshake(command_name, "phase_started", stage=phases[0])

    def _interlock_failure(self, command_name: str) -> str | None:
        """Return a PLC-like rejection reason for an unsafe command."""

        common = (
            "initialized",
            "robot_ready",
            "robot_auto",
            "robot_safe",
            "servo_ready",
            "communications_ok",
        )
        for key in common:
            if not self.interlocks.get(key, False):
                return f"PLC 互锁未满足: {key}"
        if self.interlocks.get("emergency_stop", False):
            return "PLC 互锁未满足: emergency_stop"
        if command_name == SynthesisCommand.SAMPLE.name.lower():
            if not self.interlocks.get("scanner_ready", False):
                return "PLC 互锁未满足: scanner_ready"
        if command_name in {
            SynthesisCommand.SEND_FIRING.name.lower(),
            SynthesisCommand.FETCH_FIRING.name.lower(),
        } and not self.interlocks.get("furnace_ready", False):
            return "PLC 互锁未满足: furnace_ready"
        if command_name == SynthesisCommand.FETCH_FIRING.name.lower() and not self.interlocks.get(
            "furnace_open_allowed", False
        ):
            return "PLC 互锁未满足: furnace_open_allowed"
        if command_name in {
            SynthesisCommand.ACOUSTIC_RESONANCE.name.lower(),
            SynthesisCommand.FETCH_ACOUSTIC_RESONANCE.name.lower(),
        } and not self.interlocks.get("resonance_ready", False):
            return "PLC 互锁未满足: resonance_ready"
        if command_name in {
            SynthesisCommand.FETCH_CUBIC.name.lower(),
            "add_bead",
            SynthesisCommand.DOWN_MATERIAL.name.lower(),
            SynthesisCommand.UP_MATERIAL.name.lower(),
        } and not self.interlocks.get("doors_closed", False):
            return "PLC 互锁未满足: doors_closed"
        return None

    def bind_crucible(self, crucible_id: str | None = None) -> None:
        """Complete a manual scanner response in a non-auto-scan simulation."""

        self._pending_scan = None
        self.model.bind_crucible(crucible_id or self.crucible_id)
        self._start_pending_sampling()

    def _apply_sampling_command(self, payload: tuple[int, ...]) -> None:
        if len(payload) < 81:
            raise ValueError("CMD_SAMPLE payload must contain 81 registers")
        if self._active_operation is not None:
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
        interlock_failure = self._interlock_failure(
            SynthesisCommand.SAMPLE.name.lower()
        )
        if interlock_failure:
            register = SynthesisRegisterMap().status_register(2)
            self._status_registers[register] = 3
            self._record_handshake(
                "sample_add_powder_and_beads", "failed", message=interlock_failure
            )
            return
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
        self._pending_sampling = (task_id, slot, targets)
        # A zero delay keeps the fast unit-test path; positive delays expose
        # the PLC scan-request/scan-complete boundary to callers.
        if self._auto_scan_id is None:
            # Manual scanner mode: leave the model in SCANNING until the test
            # or an external scanner calls bind_crucible().
            self._pending_scan = None
            return
        self._pending_scan = self.handshake_delay
        if self._pending_scan <= 0:
            self._pending_scan = None
            self.model.bind_crucible(self._auto_scan_id)
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
