"""Uni-Lab device wrapper for the direct YB Modbus controller.

The wrapper is intentionally thin.  Protocol framing and the scan/dosing
state machine live in ``synthesis_modbus`` and ``simulation`` respectively;
this module only exposes stable actions to Uni-Lab OS.
"""

from __future__ import annotations

from typing import Any, TypedDict

from unilabos.registry.decorators import action, device, not_action, topic_config

from yb_sse_devices.simulation import SynthesisPlcModel, SynthesisSimulationTransport
from yb_sse_devices.synthesis_direct import SynthesisDirectController
from yb_sse_devices.synthesis_modbus import ModbusTcpTransport


class StationStatusResult(TypedDict):
    """可持久化的设备状态结果；不把寄存器字典暴露给工作流。"""

    connected: bool
    simulation: bool
    ip: str
    port: int
    status: str
    sampling: int
    pause: int


class ConnectionResult(TypedDict):
    connected: bool
    simulation: bool
    ip: str
    port: int
    status: str
    error: str


class CommandResult(TypedDict):
    accepted: bool
    command: str
    status_code: int
    status_name: str


class SamplingStartedResult(TypedDict):
    accepted: bool
    command: str
    task_id: str
    slot_num: int
    material_count: int
    status_code: int
    status_name: str


class SamplingCompletedResult(TypedDict):
    accepted: bool
    command: str
    task_id: str
    slot_num: int
    material_count: int
    status_code: int
    status_name: str
    weights: list[float]
    result_codes: list[int]
    qr_code: str


class SamplingResultContract(TypedDict):
    weights: list[float]
    result_codes: list[int]
    qr_code: str


class SimulationFaultResult(TypedDict):
    name: str
    code: str
    message: str


@device(
    id="yb_synthesis_modbus_station",
    category=["workstation", "synthesis"],
    display_name="YB合成工站（Modbus直连）",
    description="设备包直接通过 Modbus TCP 控制 YB 固态电解质合成 PLC",
    icon="synthesis_station.webp",
    version="0.1.0",
)
class YBSynthesisModbusStation:
    """Direct PLC device; the default configuration is package-local dry-run."""

    def __init__(
        self,
        device_id: str | None = None,
        config: dict[str, Any] | None = None,
        ip: str = "192.168.1.10",
        port: int = 502,
        unit_id: int = 1,
        simulation: bool = True,
        simulation_crucible_id: str = "CRU-SIM-001",
        simulation_dosing_duration: float = 1.0,
        material_names: list[str] | None = None,
        **_: Any,
    ) -> None:
        resolved = dict(config or {})
        self.device_id = device_id or "yb_synthesis_modbus_station"
        self.config = resolved
        self.ip = str(resolved.get("ip", ip))
        self.port = int(resolved.get("port", port))
        self.unit_id = int(resolved.get("unit_id", unit_id))
        self.simulation = bool(resolved.get("simulation", simulation))
        self.simulation_crucible_id = str(
            resolved.get("simulation_crucible_id", simulation_crucible_id)
        )
        names = resolved.get("material_names", material_names or ())
        self.material_names = tuple(str(name).strip() for name in names)
        self.model: SynthesisPlcModel | None = None
        if self.simulation:
            self.model = SynthesisPlcModel(
                dosing_duration=float(
                    resolved.get("simulation_dosing_duration", simulation_dosing_duration)
                ),
                auto_scan_id=self.simulation_crucible_id,
            )
            transport = SynthesisSimulationTransport(
                self.model,
                crucible_id=self.simulation_crucible_id,
                material_names=self.material_names,
            )
        else:
            transport = ModbusTcpTransport(
                self.ip,
                self.port,
                unit_id=self.unit_id,
                timeout=float(resolved.get("timeout", 1.0)),
            )
        self.controller = SynthesisDirectController(
            transport,
            unit_id=self.unit_id,
            material_names=self.material_names,
        )

    @property
    @topic_config()
    def connected(self) -> bool:
        return self.controller.connected

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        """PLC 状态：IDLE / BUSY / FAULT / OFFLINE。"""
        if not self.connected:
            return "OFFLINE"
        try:
            snapshot = self.controller.status()
        except Exception:
            return "OFFLINE"
        if self.model and self.model.fault is not None:
            return "FAULT"
        active: set[int] = set()
        for key, value in snapshot.items():
            if key == "pause":
                continue
            if isinstance(value, tuple):
                active.update(int(item) for item in value)
            else:
                active.add(int(value))
        return "BUSY" if 1 in active else "IDLE"

    @property
    @topic_config(period=2.0)
    def fault(self) -> str:
        """最近一次仿真故障；真实 PLC 未提供故障文本时返回空串。"""
        if self.model and self.model.fault:
            return f"{self.model.fault.code}: {self.model.fault.message}"
        return ""

    @property
    @topic_config()
    def sampling(self) -> int:
        """称粉状态：0 空闲 / 1 运行中 / 2 完成 / 3 故障。"""
        try:
            return int(self.controller.status().get("sampling", 0))
        except Exception:
            return -1

    @property
    @topic_config()
    def furnace_01(self) -> int:
        return self._status_value("furnace")[0]

    @property
    @topic_config()
    def furnace_02(self) -> int:
        return self._status_value("furnace")[1]

    @property
    @topic_config()
    def furnace_03(self) -> int:
        return self._status_value("furnace")[2]

    @property
    @topic_config()
    def furnace_04(self) -> int:
        return self._status_value("furnace")[3]

    @property
    @topic_config()
    def joule_heating(self) -> int:
        return self._status_value("joule_heating")

    @property
    @topic_config()
    def acoustic_resonance(self) -> int:
        return self._status_value("acoustic_resonance")

    @property
    @topic_config()
    def paused(self) -> int:
        return self._status_value("pause")

    def _status_value(self, name: str) -> Any:
        try:
            value = self.controller.status().get(name, -1)
            if name == "furnace" and isinstance(value, tuple):
                return value
            if name == "furnace":
                return (-1, -1, -1, -1)
            return value
        except Exception:
            return (-1, -1, -1, -1) if name == "furnace" else -1

    @not_action
    def connect(self) -> None:
        """显式建立 PLC 连接；构造设备时不会连接。"""

        self.controller.connect()

    @not_action
    def disconnect(self) -> None:
        """显式关闭 PLC 连接。"""

        self.controller.close()

    @not_action
    def close(self) -> None:
        self.disconnect()

    def _ensure_connected(self) -> None:
        if not self.connected:
            self.connect()

    @staticmethod
    def _status_code(snapshot: dict[str, Any], command: str) -> int:
        key_by_command = {
            "down_material": "down_material",
            "up_material": "up_material",
            "sample_add_powder_and_beads": "sampling",
            "send_firing": "send_firing",
            "fetch_firing": "fetch_firing",
            "fetch_cubic": "cabin_fetch_cubic",
            "add_bead": "add_beads",
            "acoustic_resonance": "acoustic_resonance",
            "fetch_acoustic_resonance": "acoustic_fetch",
            "get_acoustic_resonance_status": "acoustic_resonance",
            "finish_acoustic_resonance": "acoustic_resonance",
            "close_cabin_door": "cabin_fetch_cubic",
            "pause": "pause",
            "resume": "pause",
        }
        value = snapshot.get(key_by_command.get(command, command), -1)
        if isinstance(value, tuple):
            return max((int(item) for item in value), default=-1)
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _status_name(code: int) -> str:
        return {0: "IDLE", 1: "RUNNING", 2: "COMPLETED", 3: "FAULT"}.get(
            int(code), "UNKNOWN"
        )

    def _command_result(self, response: dict[str, Any]) -> CommandResult:
        command = str(response.get("command", ""))
        snapshot = response.get("status")
        status = snapshot if isinstance(snapshot, dict) else {}
        code = self._status_code(status, command)
        return {
            "accepted": bool(response.get("accepted", False)),
            "command": command,
            "status_code": code,
            "status_name": self._status_name(code),
        }

    @action(always_free=True, description="查询 YB 合成 PLC 状态")
    def station_status(self) -> StationStatusResult:
        self._ensure_connected()
        snapshot = self.controller.status()
        return {
            "connected": True,
            "simulation": self.simulation,
            "ip": self.ip,
            "port": self.port,
            "status": self.status,
            "sampling": int(snapshot.get("sampling", -1)),
            "pause": int(snapshot.get("pause", -1)),
        }

    @action(always_free=True, description="测试 YB 合成 PLC Modbus 连接")
    def test_connection(self) -> ConnectionResult:
        try:
            self._ensure_connected()
            status = self.controller.status()
        except Exception as exc:
            return {
                "connected": False,
                "simulation": self.simulation,
                "ip": self.ip,
                "port": self.port,
                "status": "UNKNOWN",
                "error": str(exc),
            }
        return {
            "connected": True,
            "simulation": self.simulation,
            "ip": self.ip,
            "port": self.port,
            "status": self.status,
            "error": "",
        }

    @action(description="启动一次扫码绑定、称粉和加珠任务")
    def start_sampling(
        self,
        task_id: str = "",
        slot_num: int = 1,
        rack_positions: list[int] | None = None,
        masses: list[float] | None = None,
        tolerances: list[float] | None = None,
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: list[str] | None = None,
    ) -> SamplingStartedResult:
        self._ensure_connected()
        resolved_rack_positions = rack_positions if rack_positions is not None else [1]
        resolved_masses = masses if masses is not None else [0.9]
        resolved_tolerances = tolerances if tolerances is not None else [0.0007]
        response = self.controller.start_sampling(
            task_id=task_id,
            slot_num=slot_num,
            rack_positions=resolved_rack_positions,
            masses=resolved_masses,
            tolerances=resolved_tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
        )
        code = self._status_code(response.get("status", {}), "sample_add_powder_and_beads")
        return {
            "accepted": bool(response.get("accepted", False)),
            "command": str(response.get("command", "sample_add_powder_and_beads")),
            "task_id": str(response.get("task_id", task_id)),
            "slot_num": int(response.get("slot_num", slot_num)),
            "material_count": int(response.get("material_count", len(resolved_rack_positions))),
            "status_code": code,
            "status_name": self._status_name(code),
        }

    @action(description="完成一次称粉任务并返回二维码和重量结果")
    def sample(
        self,
        task_id: str = "",
        slot_num: int = 1,
        rack_positions: list[int] | None = None,
        masses: list[float] | None = None,
        tolerances: list[float] | None = None,
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: list[str] | None = None,
        timeout: float = 30.0,
        step: float = 0.1,
    ) -> SamplingCompletedResult:
        self._ensure_connected()
        resolved_rack_positions = rack_positions if rack_positions is not None else [1]
        resolved_masses = masses if masses is not None else [0.9]
        resolved_tolerances = tolerances if tolerances is not None else [0.0007]
        response = self.controller.run_sampling(
            task_id=task_id,
            slot_num=slot_num,
            rack_positions=resolved_rack_positions,
            masses=resolved_masses,
            tolerances=resolved_tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
            timeout=timeout,
            step=step,
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        weights = result.get("weights", {})
        result_codes = result.get("results", {})
        code = self._status_code(response.get("status", {}), "sample_add_powder_and_beads")
        return {
            "accepted": bool(response.get("accepted", False)),
            "command": str(response.get("command", "sample_add_powder_and_beads")),
            "task_id": str(response.get("task_id", task_id)),
            "slot_num": int(response.get("slot_num", slot_num)),
            "material_count": int(response.get("material_count", len(resolved_rack_positions))),
            "status_code": code,
            "status_name": self._status_name(code),
            "weights": [float(value) for value in (weights.values() if isinstance(weights, dict) else weights)],
            "result_codes": [int(value) for value in (result_codes.values() if isinstance(result_codes, dict) else result_codes)],
            "qr_code": str(result.get("qr_code", "")),
        }

    @action(always_free=True, description="读取最近一次称粉结果")
    def sampling_result(self, material_count: int | None = None) -> SamplingResultContract:
        self._ensure_connected()
        result = self.controller.sampling_result(material_count)
        weights = result.get("weights", {})
        result_codes = result.get("results", {})
        return {
            "weights": [float(value) for value in (weights.values() if isinstance(weights, dict) else weights)],
            "result_codes": [int(value) for value in (result_codes.values() if isinstance(result_codes, dict) else result_codes)],
            "qr_code": str(result.get("qr_code", "")),
        }

    @action(description="从料架向指定位置下料")
    def down_material(
        self, from_positions: list[int] | None = None, to_positions: list[int] | None = None
    ) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.down_material(from_positions or [1], to_positions or [1]))

    @action(description="从指定位置向料架上料")
    def up_material(
        self, from_positions: list[int] | None = None, to_positions: list[int] | None = None
    ) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.up_material(from_positions or [1], to_positions or [1]))

    @action(description="下发烧结或焦耳热工艺")
    def send_firing(
        self,
        position: int = 1,
        temperatures: list[int] | None = None,
        times: list[int] | None = None,
        joule_position: int = 0,
        joule_mode: int = 0,
        joule_carrier_type: int = 0,
        joule_constant_temperature: int = 0,
        joule_constant_hold_time: int = 0,
        joule_slope_temperatures: list[int] | None = None,
        joule_slope_speeds: list[int] | None = None,
        joule_slope_hold_times: list[int] | None = None,
    ) -> CommandResult:
        self._ensure_connected()
        response = self.controller.send_firing(
            position=position,
            temperatures=temperatures or [],
            times=times or [],
            joule_position=joule_position,
            joule_mode=joule_mode,
            joule_carrier_type=joule_carrier_type,
            joule_constant_temperature=joule_constant_temperature,
            joule_constant_hold_time=joule_constant_hold_time,
            joule_slope_temperatures=joule_slope_temperatures or [],
            joule_slope_speeds=joule_slope_speeds or [],
            joule_slope_hold_times=joule_slope_hold_times or [],
        )
        return self._command_result(response)

    @action(description="取出烧结炉或焦耳热载具")
    def fetch_firing(
        self, position: int = 1, joule_position: int = 0, joule_carrier_type: int = 0
    ) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.fetch_firing(
            position=position,
            joule_position=joule_position,
            joule_carrier_type=joule_carrier_type,
        ))

    @action(description="取坩埚或托盘")
    def fetch_cubic(
        self,
        source: int = 1,
        destination: int = 1,
        pallet_type: int = 1,
        slot_numbers: list[int] | None = None,
        bead_source: int = 0,
    ) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.fetch_cubic(
            source=source,
            destination=destination,
            pallet_type=pallet_type,
            slot_numbers=slot_numbers or [],
            bead_source=bead_source,
        ))

    @action(description="向坩埚加入研磨珠")
    def add_bead(self, source: int = 1) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.add_bead(source=source))

    @action(description="下发声共振工艺")
    def start_acoustic_resonance(
        self,
        fetch_position: int = 1,
        accelerations: list[int] | None = None,
        frequencies: list[int] | None = None,
        times: list[int] | None = None,
    ) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.acoustic_resonance(
            fetch_position=fetch_position,
            accelerations=accelerations or [],
            frequencies=frequencies or [],
            times=times or [],
        ))

    @action(always_free=True, description="取出声共振载具")
    def fetch_acoustic_resonance(self) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.fetch_acoustic_resonance())

    @action(always_free=True, description="查询声共振上料、下料和运行状态")
    def get_acoustic_resonance_status(self) -> CommandResult:
        self._ensure_connected()
        snapshot = self.controller.status()
        return self._command_result({
            "accepted": True,
            "command": "get_acoustic_resonance_status",
            "status": snapshot,
        })

    @action(description="结束声共振工艺并进入后续装瓶")
    def finish_acoustic_resonance(self) -> CommandResult:
        """The IO table has no separate finish register.

        Command 9 performs physical unloading; this action records the
        supervisory boundary without inventing a PLC register write.
        """
        self._ensure_connected()
        return self._command_result({
            "accepted": True,
            "command": "finish_acoustic_resonance",
            "status": self.controller.status(),
        })

    @action(description="关闭舱门")
    def close_cabin_outer_door(self, task_id: str = "") -> CommandResult:
        del task_id
        self._ensure_connected()
        return self._command_result(self.controller.close_cabin_door())

    @action(always_free=True, description="暂停 PLC 当前工艺")
    def pause(self) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.pause())

    @action(always_free=True, description="恢复 PLC 当前工艺")
    def resume(self) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.resume())

    @action(always_free=True, description="推进设备包内 PLC 仿真时钟")
    def advance_simulation(self, seconds: float = 0.1) -> StationStatusResult:
        if not self.simulation:
            raise RuntimeError("真实 PLC 模式不支持 advance_simulation")
        self._ensure_connected()
        self.controller.advance(seconds)
        return self.station_status()

    @action(always_free=True, description="注入仿真故障用于联调")
    def inject_simulation_fault(
        self,
        name: str,
        code: str = "SIM_FAULT",
        message: str = "仿真故障",
        once: bool = False,
    ) -> SimulationFaultResult:
        if not self.model:
            raise RuntimeError("真实 PLC 模式不支持仿真故障注入")
        fault = self.model.inject_fault(name, code=code, message=message, once=once)
        return {"name": fault.name, "code": fault.code, "message": fault.message}


__all__ = ["YBSynthesisModbusStation"]
