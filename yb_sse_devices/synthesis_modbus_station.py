"""Uni-Lab device wrapper for the direct YB Modbus controller.

The wrapper is intentionally thin.  Protocol framing and the scan/dosing
state machine live in ``synthesis_modbus`` and ``simulation`` respectively;
this module only exposes stable actions to Uni-Lab OS.
"""

from __future__ import annotations

from typing import Any, Sequence

from unilabos.registry.decorators import action, device, not_action

from yb_sse_devices.simulation import SynthesisPlcModel, SynthesisSimulationTransport
from yb_sse_devices.synthesis_direct import SynthesisDirectController
from yb_sse_devices.synthesis_modbus import ModbusTcpTransport


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
        material_names: Sequence[str] | None = None,
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
    def connected(self) -> bool:
        return self.controller.connected

    @not_action
    def close(self) -> None:
        self.controller.close()

    def _ensure_connected(self) -> None:
        if not self.connected:
            self.controller.connect()

    @action(always_free=True, description="查询 YB 合成 PLC 状态")
    def station_status(self) -> dict[str, Any]:
        self._ensure_connected()
        return {
            "connected": True,
            "simulation": self.simulation,
            "ip": self.ip,
            "port": self.port,
            "status": self.controller.status(),
        }

    @action(always_free=True, description="测试 YB 合成 PLC Modbus 连接")
    def test_connection(self) -> dict[str, Any]:
        try:
            self._ensure_connected()
            status = self.controller.status()
        except Exception as exc:
            return {
                "connected": False,
                "simulation": self.simulation,
                "ip": self.ip,
                "port": self.port,
                "error": str(exc),
            }
        return {
            "connected": True,
            "simulation": self.simulation,
            "ip": self.ip,
            "port": self.port,
            "status": status,
        }

    @action(description="启动一次扫码绑定、称粉和加珠任务")
    def start_sampling(
        self,
        task_id: str = "",
        slot_num: int = 1,
        rack_positions: Sequence[int] = (1,),
        masses: Sequence[float] = (0.9,),
        tolerances: Sequence[float] = (0.0007,),
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        self._ensure_connected()
        return self.controller.start_sampling(
            task_id=task_id,
            slot_num=slot_num,
            rack_positions=rack_positions,
            masses=masses,
            tolerances=tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
        )

    @action(description="完成一次称粉任务并返回二维码和重量结果")
    def sample(
        self,
        task_id: str = "",
        slot_num: int = 1,
        rack_positions: Sequence[int] = (1,),
        masses: Sequence[float] = (0.9,),
        tolerances: Sequence[float] = (0.0007,),
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: Sequence[str] | None = None,
        timeout: float = 30.0,
        step: float = 0.1,
    ) -> dict[str, Any]:
        self._ensure_connected()
        return self.controller.run_sampling(
            task_id=task_id,
            slot_num=slot_num,
            rack_positions=rack_positions,
            masses=masses,
            tolerances=tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
            timeout=timeout,
            step=step,
        )

    @action(always_free=True, description="读取最近一次称粉结果")
    def sampling_result(self, material_count: int | None = None) -> dict[str, Any]:
        self._ensure_connected()
        return self.controller.sampling_result(material_count)

    @action(always_free=True, description="推进设备包内 PLC 仿真时钟")
    def advance_simulation(self, seconds: float = 0.1) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        if not self.model:
            raise RuntimeError("真实 PLC 模式不支持仿真故障注入")
        fault = self.model.inject_fault(name, code=code, message=message, once=once)
        return {"name": fault.name, "code": fault.code, "message": fault.message}


__all__ = ["YBSynthesisModbusStation"]
