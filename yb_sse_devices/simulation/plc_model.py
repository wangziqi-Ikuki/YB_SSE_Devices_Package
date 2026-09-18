"""可控时钟驱动的 YB 合成 PLC 行为模型。

这个模块模拟的是 PLC/机器人在“内部扫码绑定坩埚 → 称粉”的最小行为，
而不是高层 TCP JSON 服务。它没有 socket、线程或真实 IO，所有状态推进都
通过 :meth:`SynthesisPlcModel.advance` 明确触发，因此测试可以稳定重放。

典型用法::

    sim = SynthesisPlcModel()
    sim.begin_crucible_binding("TASK-1", 1, expected_crucible_id="CRU-1")
    sim.bind_crucible("CRU-1")
    sim.start_sampling("TASK-1", 1, {"Li2S": 0.9})
    sim.advance(2.0)
    assert sim.sampling_result is not None

Modbus 适配器可以把 ``phase``, ``sampling_result`` 和
``holding_registers`` 映射到实际寄存器；dry-run 可以直接使用这些领域
方法。这样底层协议和上层 CmdServer Mock 不会耦合。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class SimulationError(RuntimeError):
    """仿真动作违反当前状态或参数约束。"""


class SamplingPhase(str, Enum):
    """内部扫码和称粉的最小状态机。"""

    IDLE = "idle"
    SCANNING = "scanning"
    BOUND = "bound"
    DOSING = "dosing"
    COMPLETED = "completed"
    FAULT = "fault"


@dataclass(frozen=True)
class SimulationClock:
    """确定性时钟。

        ``advance`` 返回新的时钟对象；不会休眠，也不会读取墙上时钟。适合单测、
    dry-run 和可重复的故障场景。
    """

    _value: float = 0.0

    @property
    def now(self) -> float:
        return self._value

    def advance(self, seconds: float) -> "SimulationClock":
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            raise ValueError("seconds 必须是有限的非负数")
        return SimulationClock(self._value + value)

    def set(self, value: float) -> "SimulationClock":
        value = float(value)
        if not math.isfinite(value) or value < self._value:
            raise ValueError("时钟只能前进到有限的非负时间")
        return SimulationClock(value)


@dataclass(frozen=True)
class FaultInjection:
    """一个可命名的故障注入。``once`` 表示触发后自动移除。"""

    name: str
    code: str = "SIM_FAULT"
    message: str = "仿真故障"
    once: bool = False


@dataclass(frozen=True)
class BoundCrucible:
    task_id: str
    slot_num: int
    crucible_id: str
    expected_crucible_id: str = ""
    cubic_type: int | None = None
    bound_at: float = 0.0


@dataclass(frozen=True)
class SamplingResult:
    task_id: str
    slot_num: int
    crucible_id: str
    weights: dict[str, float]
    results: dict[str, int]
    qr_code: str
    completed_at: float


@dataclass(frozen=True)
class SimulationEvent:
    at: float
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


class SynthesisPlcModel:
    """YB 合成 PLC 的可控、无网络行为模型。

    该模型只覆盖称粉前的内部扫码绑定和称粉结果，后续炉次等行为可以在
    同一接口上扩展。动作返回 ``None`` 或对象，非法时抛出
    :class:`SimulationError`，方便设备包将其转换成自己的 action contract。
    """

    # 与现场上位机读取逻辑保持一致：称粉重量从 40120 开始，结果从
    # 40150 开始，扫码文本从 40170 开始。这里的映射只用于测试/适配器，
    # 并不声称取代最终 IO 表。
    SAMPLE_STATUS_REGISTER = 40102
    WEIGHT_START_REGISTER = 40120
    RESULT_START_REGISTER = 40150
    QR_START_REGISTER = 40170
    QR_REGISTER_COUNT = 20

    _PHASE_STATUS = {
        SamplingPhase.IDLE: 0,
        SamplingPhase.SCANNING: 1,
        SamplingPhase.BOUND: 1,
        SamplingPhase.DOSING: 1,
        SamplingPhase.COMPLETED: 2,
        SamplingPhase.FAULT: 3,
    }

    def __init__(
        self,
        *,
        clock: SimulationClock | None = None,
        scan_timeout: float = 15.0,
        dosing_duration: float = 1.0,
        auto_scan_id: str | None = None,
    ) -> None:
        if scan_timeout <= 0 or dosing_duration < 0:
            raise ValueError("scan_timeout 必须大于 0，dosing_duration 不能为负数")
        self.clock = clock or SimulationClock()
        self.scan_timeout = float(scan_timeout)
        self.dosing_duration = float(dosing_duration)
        self.auto_scan_id = str(auto_scan_id).strip() if auto_scan_id else None
        self.phase = SamplingPhase.IDLE
        self.bound_crucibles: dict[tuple[str, int], BoundCrucible] = {}
        self.current_binding: BoundCrucible | None = None
        self.current_sample: dict[str, Any] | None = None
        self.sampling_result: SamplingResult | None = None
        self.fault: FaultInjection | None = None
        self._faults: dict[str, FaultInjection] = {}
        self.events: list[SimulationEvent] = []

    # ------------------------------------------------------------------
    # Lifecycle and fault controls
    # ------------------------------------------------------------------
    def reset(self, *, keep_faults: bool = False) -> None:
        """回到空闲状态并清理绑定/称粉结果。"""

        self.phase = SamplingPhase.IDLE
        self.bound_crucibles.clear()
        self.current_binding = None
        self.current_sample = None
        self.sampling_result = None
        self.fault = None
        self.events.clear()
        if not keep_faults:
            self._faults.clear()

    def inject_fault(
        self,
        name: str,
        *,
        code: str = "SIM_FAULT",
        message: str = "仿真故障",
        once: bool = False,
    ) -> FaultInjection:
        """注册故障。

        约定名称：``scanner``/``scan_timeout`` 影响扫码，``scale`` 影响称粉，
        ``dosing`` 在称粉完成前触发。未知名称也会被保存，便于上层测试自定义
        故障映射。
        """

        key = str(name).strip()
        if not key:
            raise ValueError("故障名称不能为空")
        fault = FaultInjection(key, str(code), str(message), bool(once))
        self._faults[key] = fault
        return fault

    def clear_fault(self, name: str | None = None) -> None:
        if name is None:
            self._faults.clear()
            self.fault = None
            if self.phase is SamplingPhase.FAULT:
                self.phase = SamplingPhase.IDLE
            return
        self._faults.pop(str(name), None)
        if self.fault and self.fault.name == str(name):
            self.fault = None

    def _take_fault(self, *names: str) -> FaultInjection | None:
        for name in names:
            fault = self._faults.get(name)
            if fault is not None:
                if fault.once:
                    self._faults.pop(name, None)
                return fault
        return None

    def _enter_fault(self, fault: FaultInjection) -> None:
        self.fault = fault
        self.phase = SamplingPhase.FAULT
        self.events.append(
            SimulationEvent(
                self.clock.now,
                "fault",
                {"name": fault.name, "code": fault.code, "message": fault.message},
            )
        )

    # ------------------------------------------------------------------
    # Internal scanner and sampling state machine
    # ------------------------------------------------------------------
    def begin_crucible_binding(
        self,
        task_id: str,
        slot_num: int,
        *,
        expected_crucible_id: str | None = None,
        cubic_type: int | None = None,
    ) -> None:
        """模拟机械臂把坩埚送到内部扫码位并发出扫码请求。"""

        self._require_idle_or_bound()
        task = str(task_id).strip()
        slot = int(slot_num)
        if not task or slot <= 0:
            raise ValueError("task_id 不能为空，slot_num 必须为正数")
        fault = self._take_fault("scanner", "bind_crucible")
        if fault:
            self._enter_fault(fault)
            return
        self.current_binding = BoundCrucible(
            task_id=task,
            slot_num=slot,
            crucible_id="",
            expected_crucible_id=str(expected_crucible_id or "").strip(),
            cubic_type=cubic_type,
            bound_at=self.clock.now,
        )
        self.phase = SamplingPhase.SCANNING
        self.events.append(
            SimulationEvent(
                self.clock.now,
                "scan_requested",
                {"task_id": task, "slot_num": slot},
            )
        )

    def bind_crucible(self, crucible_id: str, *, cubic_type: int | None = None) -> BoundCrucible:
        """提交内部扫码结果并绑定坩埚。"""

        if self.phase is not SamplingPhase.SCANNING or self.current_binding is None:
            raise SimulationError("当前没有等待内部扫码")
        qr_code = str(crucible_id).strip()
        if not qr_code:
            fault = FaultInjection("empty_qr", "QR_EMPTY", "扫码结果为空")
            self._enter_fault(fault)
            raise SimulationError(fault.message)
        fault = self._take_fault("scanner_result", "qr_unknown")
        if fault:
            self._enter_fault(fault)
            raise SimulationError(fault.message)
        expected = self.current_binding.expected_crucible_id
        if expected and qr_code != expected:
            fault = FaultInjection("qr_mismatch", "QR_MISMATCH", "坩埚二维码与任务不匹配")
            self._enter_fault(fault)
            raise SimulationError(fault.message)
        expected_type = self.current_binding.cubic_type
        if expected_type is not None and cubic_type is not None and cubic_type != expected_type:
            fault = FaultInjection("cubic_type_mismatch", "CUBIC_TYPE_MISMATCH", "坩埚类型与任务不匹配")
            self._enter_fault(fault)
            raise SimulationError(fault.message)
        bound = BoundCrucible(
            task_id=self.current_binding.task_id,
            slot_num=self.current_binding.slot_num,
            crucible_id=qr_code,
            expected_crucible_id=self.current_binding.expected_crucible_id,
            cubic_type=cubic_type if cubic_type is not None else self.current_binding.cubic_type,
            bound_at=self.clock.now,
        )
        key = (bound.task_id, bound.slot_num)
        old = self.bound_crucibles.get(key)
        if old and old.crucible_id != bound.crucible_id:
            fault = FaultInjection("duplicate_binding", "QR_BOUND", "任务槽位已经绑定其他坩埚")
            self._enter_fault(fault)
            raise SimulationError(fault.message)
        self.bound_crucibles[key] = bound
        self.current_binding = bound
        self.phase = SamplingPhase.BOUND
        self.events.append(
            SimulationEvent(
                self.clock.now,
                "crucible_bound",
                {
                    "task_id": bound.task_id,
                    "slot_num": bound.slot_num,
                    "crucible_id": bound.crucible_id,
                },
            )
        )
        return bound

    def start_sampling(
        self,
        task_id: str,
        slot_num: int,
        target_weights: Mapping[str, float],
        *,
        expected_results: Mapping[str, int] | None = None,
        actual_weights: Mapping[str, float] | None = None,
    ) -> None:
        """扫码绑定成功后开始称粉。

        ``target_weights`` 是工艺目标；仿真默认按目标返回实际重量。测试可以
        通过 ``actual_weights`` 覆盖某些结果，验证公差或超差处理。
        """

        if self.phase is not SamplingPhase.BOUND or self.current_binding is None:
            raise SimulationError("称粉前必须先完成坩埚扫码绑定")
        key = (str(task_id).strip(), int(slot_num))
        binding = self.bound_crucibles.get(key)
        if binding is None or binding is not self.current_binding:
            raise SimulationError("当前任务槽位没有有效的坩埚绑定")
        if not target_weights:
            raise ValueError("target_weights 不能为空")
        targets = self._validate_weights(target_weights)
        actuals = self._validate_weights(actual_weights or targets)
        if set(actuals) != set(targets):
            raise ValueError("actual_weights 必须与 target_weights 使用相同的物料名")
        fault = self._take_fault("scale")
        if fault:
            self._enter_fault(fault)
            return
        self.current_sample = {
            "task_id": key[0],
            "slot_num": key[1],
            "target_weights": targets,
            "actual_weights": actuals,
            "results": {name: int((expected_results or {}).get(name, 0)) for name in targets},
            "started_at": self.clock.now,
        }
        self.sampling_result = None
        self.phase = SamplingPhase.DOSING
        self.events.append(
            SimulationEvent(
                self.clock.now,
                "dosing_started",
                {"task_id": key[0], "slot_num": key[1], "materials": list(targets)},
            )
        )

    def advance(self, seconds: float) -> None:
        """推进仿真时间并执行到期状态转换。"""

        self.clock = self.clock.advance(seconds)
        if self.phase is SamplingPhase.SCANNING:
            binding = self.current_binding
            elapsed = self.clock.now - (binding.bound_at if binding else self.clock.now)
            if elapsed >= self.scan_timeout:
                fault = self._take_fault("scan_timeout") or FaultInjection(
                    "scan_timeout", "SCAN_TIMEOUT", "内部扫码超时"
                )
                self._enter_fault(fault)
            elif self.auto_scan_id and binding and elapsed >= 0:
                # auto_scan_id 仅用于 dry-run 快速演示；正式协议测试应显式
                # 调用 bind_crucible，避免自动跳过扫码门禁。
                self.bind_crucible(self.auto_scan_id)
        if self.phase is SamplingPhase.DOSING and self.current_sample:
            started = float(self.current_sample["started_at"])
            if self.clock.now - started >= self.dosing_duration:
                fault = self._take_fault("dosing", "scale")
                if fault:
                    self._enter_fault(fault)
                    return
                binding = self.current_binding
                if binding is None:
                    raise SimulationError("称粉完成时缺少坩埚绑定")
                self.sampling_result = SamplingResult(
                    task_id=self.current_sample["task_id"],
                    slot_num=self.current_sample["slot_num"],
                    crucible_id=binding.crucible_id,
                    weights=dict(self.current_sample["actual_weights"]),
                    results=dict(self.current_sample["results"]),
                    qr_code=binding.crucible_id,
                    completed_at=self.clock.now,
                )
                self.phase = SamplingPhase.COMPLETED
                self.events.append(
                    SimulationEvent(
                        self.clock.now,
                        "dosing_completed",
                        {"task_id": self.sampling_result.task_id, "slot_num": self.sampling_result.slot_num},
                    )
                )

    # Alias makes call sites explicit about simulated time.
    tick = advance

    def _require_idle_or_bound(self) -> None:
        if self.phase not in {SamplingPhase.IDLE, SamplingPhase.COMPLETED, SamplingPhase.BOUND}:
            raise SimulationError(f"当前状态 {self.phase.value} 不允许开始扫码")

    @staticmethod
    def _validate_weights(values: Mapping[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for name, value in values.items():
            key = str(name).strip()
            number = float(value)
            if not key or not math.isfinite(number) or number <= 0:
                raise ValueError("物料名称不能为空，重量必须是有限正数")
            result[key] = number
        return result

    # ------------------------------------------------------------------
    # Read views for dry-run and a future Modbus adapter
    # ------------------------------------------------------------------
    @property
    def sampling_status(self) -> int:
        return self._PHASE_STATUS[self.phase]

    @property
    def connected(self) -> bool:
        """仿真后端是否可用；工艺故障不会伪装成 TCP 断线。"""

        return True

    def snapshot(self) -> dict[str, Any]:
        binding = self.current_binding
        result = self.sampling_result
        return {
            "phase": self.phase.value,
            "sampling_status": self.sampling_status,
            "fault": (
                {"name": self.fault.name, "code": self.fault.code, "message": self.fault.message}
                if self.fault
                else None
            ),
            "binding": (
                {
                    "task_id": binding.task_id,
                    "slot_num": binding.slot_num,
                    "crucible_id": binding.crucible_id,
                    "cubic_type": binding.cubic_type,
                }
                if binding
                else None
            ),
            "result": (
                {
                    "task_id": result.task_id,
                    "slot_num": result.slot_num,
                    "crucible_id": result.crucible_id,
                    "weights": dict(result.weights),
                    "results": dict(result.results),
                    "qr_code": result.qr_code,
                    "completed_at": result.completed_at,
                }
                if result
                else None
            ),
        }

    def holding_registers(self, *, start: int = 40001, count: int = 189) -> list[int]:
        """返回一段 Holding Register 快照。

        仅填充上位机当前使用的采样区；未定义寄存器返回零。浮点数采用与
        ``PlcModbusClient`` 相同的低字在前顺序，二维码每个寄存器低字节在前。
        """

        if start < 1 or count < 0:
            raise ValueError("start 必须为正数，count 不能为负数")
        registers = [0] * count

        def put(address: int, value: int) -> None:
            index = address - start
            if 0 <= index < count:
                registers[index] = int(value) & 0xFFFF

        put(self.SAMPLE_STATUS_REGISTER, self.sampling_status)
        result = self.sampling_result
        if result is None:
            return registers
        for index, value in enumerate(result.weights.values()):
            packed = struct.unpack("<I", struct.pack("<f", float(value)))[0]
            put(self.WEIGHT_START_REGISTER + index * 2, packed & 0xFFFF)
            put(self.WEIGHT_START_REGISTER + index * 2 + 1, packed >> 16)
        for index, value in enumerate(result.results.values()):
            put(self.RESULT_START_REGISTER + index, value)
        encoded = result.qr_code.encode("ascii", errors="replace")[: self.QR_REGISTER_COUNT * 2]
        encoded += b"\x00" * (self.QR_REGISTER_COUNT * 2 - len(encoded))
        for index in range(self.QR_REGISTER_COUNT):
            put(self.QR_START_REGISTER + index, encoded[index * 2] | (encoded[index * 2 + 1] << 8))
        return registers

    def read_register(self, address: int) -> int:
        return self.holding_registers(start=int(address), count=1)[0]


# Name used by callers that treat this object as a complete simulation backend.
SynthesisPlcSimulation = SynthesisPlcModel
