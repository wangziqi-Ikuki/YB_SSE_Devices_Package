"""Uni-Lab device wrapper for the direct YB Modbus controller.

The wrapper is intentionally thin.  Protocol framing and the scan/dosing
state machine live in ``synthesis_modbus`` and ``simulation`` respectively;
this module only exposes stable actions to Uni-Lab OS.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from itertools import count
from typing import Annotated, Any, TypedDict

from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.decorators import NodeType, action, device, not_action, topic_config
from unilabos.registry.placeholder_type import ResourceSlot

from yb_sse_devices.resources.synthesis_crucible.resource import SynthesisCrucible
from yb_sse_devices.simulation import SynthesisPlcModel, SynthesisSimulationTransport
from yb_sse_devices.devices.yb_synthesis_modbus_station.controller import SynthesisDirectController
from yb_sse_devices.devices.yb_synthesis_modbus_station.modbus import (
    ModbusTcpTransport,
    cubic_source_to_plc,
)
from yb_sse_devices.common.synthesis_catalog import (
    cubic_type_code,
    derive_pallet_slot_types,
    pallet_type_code,
    rack_material_positions,
    recipe_spec,
)
from yb_sse_devices.common.synthesis_protocol import (
    flatten_recipe_param,
    resolve_recipe_materials_input,
    resolve_task_slots_input,
)


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
    success: bool
    message: str
    command: str
    status_code: int
    status_name: str


class Rack2DoseResult(TypedDict):
    """PLC 命令3按槽位执行后的回读结果。"""

    success: bool
    message: str
    command: str
    slot_nums: list[int]
    qr_codes: list[str]
    weights: list[float]


class SamplingStartedResult(TypedDict):
    accepted: bool
    success: bool
    message: str
    command: str
    task_id: str
    slot_num: int
    material_count: int
    status_code: int
    status_name: str


class SamplingCompletedResult(TypedDict):
    accepted: bool
    success: bool
    message: str
    command: str
    task_id: str
    slot_num: int
    material_count: int
    status_code: int
    status_name: str
    weights: list[float]
    result_codes: list[int]
    qr_code: str


class MaterialSamplingResult(TypedDict):
    """称粉动作的物料感知结果。

    ``crucible`` 是同名 ResourceSlot 透传，方便 OS 在动作完成后继续维护
    物料链；条码和称量结果来自 PLC 回读，不能由工作流参数代替。
    """

    accepted: bool
    success: bool
    command: str
    task_id: str
    slot_num: int
    material_count: int
    status_code: int
    status_name: str
    weights: list[float]
    result_codes: list[int]
    qr_code: str
    crucible: ResourceSlot
    source_site: str
    message: str


class SamplingResultContract(TypedDict):
    weights: list[float]
    result_codes: list[int]
    qr_code: str


class SimulationFaultResult(TypedDict):
    name: str
    code: str
    message: str


class SimulationInterlockResult(TypedDict):
    name: str
    allowed: bool
    initialized: bool
    emergency_stop: bool
    doors_closed: bool
    robot_ready: bool
    robot_auto: bool
    robot_safe: bool
    servo_ready: bool
    communications_ok: bool
    scanner_ready: bool
    furnace_ready: bool
    furnace_open_allowed: bool
    resonance_ready: bool


class ModbusActionResult(TypedDict):
    """Stable envelope for protocol compatibility actions."""

    result: int
    accepted: bool
    success: bool
    message: str
    data: str


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
        simulation_handshake_delay: float = 0.1,
        simulation_failure_plan: Mapping[str, str] | None = None,
        simulation_handshake_history_limit: int = 100,
        material_names: list[str] | None = None,
        business_simulation: Any = None,
        business_state: Any = None,
        business_simulation_auto_advance: bool = False,
        business_simulation_step_interval: float = 0.1,
        business_simulation_load_demo: bool = False,
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
        # The Modbus PLC simulator models registers and device motion.  The
        # old Qt bridge also exposed recipe/TASK/POST/LOT bookkeeping which is
        # a separate business contract.  Keep that contract injectable so the
        # direct package can exercise the complete workflow without inventing
        # registers that are absent from the IO table.
        configured_business = resolved.get("business_simulation", business_simulation)
        configured_state = resolved.get("business_state", business_state)
        self.business_state: Any = configured_state
        if self.business_state is None and configured_business is not False:
            if configured_business is not None and (
                callable(getattr(configured_business, "handle", None))
                or callable(getattr(configured_business, "execute", None))
            ):
                self.business_state = configured_business
            elif self.simulation or bool(configured_business):
                try:
                    from yb_sse_devices.simulation.business import BusinessSimulation
                except ModuleNotFoundError:
                    # Keep this branch compatible with older package checkouts
                    # where the richer deterministic simulator is not present.
                    from yb_sse_devices.synthesis_mock_server import MockSynthesisState

                    self.business_state = MockSynthesisState(
                        auto_advance=bool(
                            resolved.get(
                                "business_simulation_auto_advance",
                                business_simulation_auto_advance,
                            )
                        ),
                        step_interval=float(
                            resolved.get(
                                "business_simulation_step_interval",
                                business_simulation_step_interval,
                            )
                        ),
                        load_demo=bool(
                            resolved.get(
                                "business_simulation_load_demo",
                                business_simulation_load_demo,
                            )
                        ),
                    )
                else:
                    # BusinessSimulation uses a deterministic clock.  The
                    # legacy step/auto settings remain compatibility knobs;
                    # callers advance it explicitly through this station.
                    self.business_state = BusinessSimulation(
                        acoustic_duration=float(
                            resolved.get("business_acoustic_duration", 1.0)
                        ),
                        sampling_duration=float(
                            resolved.get("business_sampling_duration", 0.5)
                        ),
                        firing_duration=float(
                            resolved.get("business_firing_duration", 2.0)
                        ),
                        auto_advance=bool(
                            resolved.get(
                                "business_simulation_auto_advance",
                                business_simulation_auto_advance,
                            )
                        ),
                        step_interval=float(
                            resolved.get(
                                "business_simulation_step_interval",
                                business_simulation_step_interval,
                            )
                        ),
                        load_demo=bool(
                            resolved.get(
                                "business_simulation_load_demo",
                                business_simulation_load_demo,
                            )
                        ),
                    )
        self._business_request_ids = count(1)
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
                handshake_delay=float(
                    resolved.get("simulation_handshake_delay", simulation_handshake_delay)
                ),
                failure_plan=resolved.get(
                    "simulation_failure_plan", simulation_failure_plan or {}
                ),
                handshake_history_limit=int(
                    resolved.get(
                        "simulation_handshake_history_limit",
                        simulation_handshake_history_limit,
                    )
                ),
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
            plc_units=not self.simulation,
        )

    @property
    @topic_config()
    def connected(self) -> bool:
        return self.controller.connected

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        """PLC 状态：IDLE / BUSY / PAUSED / FAULT / OFFLINE。"""
        if not self.connected:
            return "OFFLINE"
        try:
            snapshot = self.controller.status()
        except Exception:
            return "OFFLINE"
        if self.model and self.model.fault is not None:
            return "FAULT"
        codes: set[int] = set()
        active: set[int] = set()
        for key, value in snapshot.items():
            if key == "pause":
                continue
            if isinstance(value, tuple):
                values = {int(item) for item in value}
            else:
                values = {int(value)}
            codes.update(values)
            active.update(values)
        if 3 in codes:
            return "FAULT"
        if int(snapshot.get("pause", 0)) == 1:
            return "PAUSED"
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
    def acoustic_process(self) -> int:
        """声共振设备本体状态（PLC 40109）。"""

        return self._status_value("acoustic_process")

    @property
    @topic_config()
    def cabin_feed_state(self) -> int:
        """方舱进料状态（PLC 40108），不是 command 7 的任务状态。"""

        return self._status_value("cabin_feed_state")

    @property
    @topic_config()
    def paused(self) -> int:
        return self._status_value("pause")

    @property
    @topic_config()
    def operation_phase(self) -> str:
        """Current staged PLC phase exposed by the local simulator."""

        snapshotter = getattr(self.controller.transport, "operation_snapshot", None)
        if not callable(snapshotter):
            return "unknown"
        return str(snapshotter().get("phase", "idle"))

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
        reconnect_business = getattr(self.business_state, "reconnect", None)
        if callable(reconnect_business):
            reconnect_business()

    @not_action
    def disconnect(self) -> None:
        """显式关闭 PLC 连接。"""

        self.controller.close()
        disconnect_business = getattr(self.business_state, "disconnect", None)
        if callable(disconnect_business):
            disconnect_business()

    @not_action
    def close(self) -> None:
        self.disconnect()

    def _ensure_connected(self) -> None:
        if not self.connected:
            self.connect()

    def _business_call(
        self, action_name: str, param: Mapping[str, Any] | None = None
    ) -> ModbusActionResult:
        """Run a business action against the optional package-local simulator.

        Recipe/task/post/LOT state is not part of the currently agreed PLC IO
        table.  In simulation mode it is provided by ``MockSynthesisState``;
        in real Modbus mode an explicit unsupported response is returned
        instead of silently writing a made-up register.
        """

        payload = dict(param or {})
        state = self.business_state
        if state is None:
            return {
                "request_id": next(self._business_request_ids),
                "result": 14,
                "accepted": False,
                "success": False,
                "not_supported": True,
                "command": action_name,
                "message": (
                    f"真实 Modbus PLC 未提供业务动作 {action_name}；"
                    "请使用已注入的业务模拟器或补充正式 IO 协议"
                ),
            }
        handler = getattr(state, "handle", None)
        executor = getattr(state, "execute", None)
        if callable(handler):
            response = handler(
                {
                    "request_id": next(self._business_request_ids),
                    "action": action_name,
                    "param": payload,
                }
            )
        elif callable(executor):
            # BusinessSimulation's query actions intentionally have no filter
            # arguments; preserve their canonical return shape here and apply
            # the optional filters below.
            execute_payload = payload
            if action_name in {"query_tasks", "query_posts", "station_status"}:
                execute_payload = {}
            response = executor(action_name, **execute_payload)
        else:
            method = getattr(state, action_name, None)
            if not callable(method):
                response = None
            else:
                try:
                    response = method(**payload)
                except TypeError:
                    if action_name in {"query_tasks", "query_posts", "station_status"}:
                        response = method()
                    else:
                        raise
        if not isinstance(response, dict):
            return {
                "result": 2,
                "accepted": False,
                "success": False,
                "command": action_name,
                "message": "业务模拟器返回格式无效",
            }
        result = response.get("result", 2)
        try:
            ok = int(result) == 0
        except (TypeError, ValueError):
            ok = False
        response.setdefault("accepted", ok)
        response.setdefault("success", ok)
        response.setdefault("not_supported", False)
        response.setdefault("command", action_name)
        data = response.get("data")
        if isinstance(data, dict) and action_name == "create_task" and data.get("task_id"):
            response.setdefault("task_id", str(data["task_id"]))
        if action_name in {
            "query_tasks", "start_task", "upload_cubic", "confirm_recipe",
            "start_recipt", "get_recipt_status", "start_acoustic_resonance",
            "get_acoustic_resonance_status", "fetch_acoustic_resonance",
            "finish_acoustic_resonance", "scan_big_cubic_to_bottle",
        } and payload.get("task_id"):
            response.setdefault("task_id", str(payload["task_id"]))
        if action_name in {
            "start_sintering", "get_sintering_status", "fetch_joule_heating",
            "fetch_furnace",
        } and payload.get("post_id"):
            response.setdefault("post_id", str(payload["post_id"]))
        return response

    @staticmethod
    def _business_task_param(task_id: str = "", extra: Mapping[str, Any] | None = None) -> ModbusActionResult:
        payload = dict(extra or {})
        text = str(task_id or "").strip()
        if text:
            payload["task_id"] = text
        return payload

    @staticmethod
    def _business_post_param(post_id: str = "", extra: Mapping[str, Any] | None = None) -> ModbusActionResult:
        payload = dict(extra or {})
        text = str(post_id or "").strip()
        if text:
            payload["post_id"] = text
        return payload

    @staticmethod
    def _status_code(snapshot: dict[str, Any], command: str) -> int:
        key_by_command = {
            "down_material": "down_material",
            "up_material": "up_material",
            "sample_add_powder_and_beads": "sampling",
            "send_firing": "send_firing",
            "fetch_firing": "fetch_firing",
            # Command 7 completes on PLC register 40106 (upper-pallet task).
            # Register 40108 is the cabin feed state and must not be treated
            # as the command-7 task result.
            "fetch_cubic": "upper_pallet",
            "add_bead": "add_beads",
            "acoustic_resonance": "acoustic_resonance",
            "fetch_acoustic_resonance": "acoustic_fetch",
            "get_acoustic_resonance_status": "acoustic_resonance",
            "finish_acoustic_resonance": "acoustic_resonance",
            "close_cabin_door": "cabin_feed_state",
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

    @staticmethod
    def _resource_identity(resource: ResourceSlot) -> str:
        """Extract the field barcode from a runtime ResourceSlot when present."""

        if isinstance(resource, str):
            return resource.strip()
        if isinstance(resource, Mapping):
            for key in ("barcode", "qr_code", "crucible_id", "resource_id", "uuid", "id"):
                value = resource.get(key)
                if value:
                    return str(value).strip()
        for key in ("barcode", "qr_code", "crucible_id", "resource_id", "uuid", "id"):
            value = getattr(resource, key, None)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _slot_from_site(source_site: str) -> int | None:
        """Map the package's ``rack-<zero based index>`` site labels to slots."""

        label = str(source_site).strip()
        if not label:
            return None
        suffix = label.rsplit("-", 1)[-1]
        try:
            index = int(suffix)
        except ValueError:
            return None
        return index + 1 if index >= 0 else None

    def _command_result(self, response: dict[str, Any]) -> CommandResult:
        command = str(response.get("command", ""))
        snapshot = response.get("status")
        status = snapshot if isinstance(snapshot, dict) else {}
        code = self._status_code(status, command)
        return {
            "accepted": bool(response.get("accepted", False)),
            "success": bool(response.get("accepted", False)) and code != 3,
            "message": "" if code != 3 else "PLC 报告故障状态",
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

    @action(always_free=True, description="读取 PLC 状态并确认连接仍然可用")
    def check_connection(self) -> ConnectionResult:
        try:
            self._ensure_connected()
            if not self.controller.check_connection():
                raise RuntimeError("PLC 状态读取失败")
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

    @action(always_free=True, description="断开后重新连接 YB 合成 PLC")
    def reconnect(self) -> ConnectionResult:
        try:
            self.controller.reconnect()
            reconnect_business = getattr(self.business_state, "reconnect", None)
            if callable(reconnect_business):
                reconnect_business()
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

    @action(always_free=True, description="查询 TASK 任务队列")
    def query_tasks(self, from_id: str = "", only_running: bool = False) -> ModbusActionResult:
        return self._business_call(
            "query_tasks", {"fromId": str(from_id or "").strip(), "onlyRunning": bool(only_running)}
        )

    @action(always_free=True, description="查询 POST 后处理任务队列")
    def query_posts(self, from_id: str = "", only_running: bool = False) -> ModbusActionResult:
        return self._business_call(
            "query_posts", {"fromId": str(from_id or "").strip(), "onlyRunning": bool(only_running)}
        )

    @action(always_free=True, description="按 LOT 编号查询配方、加样与分烧数据")
    def query_lot(self, lot_id: str) -> ModbusActionResult:
        return self._business_call("query_lot", {"lotId": str(lot_id or "").strip()})

    @action(always_free=True, description="按装瓶二维码查询对应 LOT 与小坩埚")
    def query_bottle_code(self, bottle_code: str) -> ModbusActionResult:
        return self._business_call(
            "query_bottle_code", {"bottle_code": str(bottle_code or "").strip()}
        )

    @action(description="创建 POST 后处理任务")
    def create_post(self, task_id: str = "", post_id: str = "") -> ModbusActionResult:
        return self._business_call(
            "create_post", {"task_id": str(task_id or "").strip(), "post_id": str(post_id or "").strip()}
        )

    @action(description="启动 POST 后处理任务")
    def start_post(self, post_id: str = "") -> ModbusActionResult:
        return self._business_call("start_post", {"post_id": str(post_id or "").strip()})

    @action(description="扫描 LOT 到批次")
    def scan_lot_to_batch(
        self, post_id: str = "", lot_id: str = "", qrcode: str = ""
    ) -> ModbusActionResult:
        return self._business_call(
            "scan_lot_to_batch",
            {"post_id": str(post_id or "").strip(), "lot_id": str(lot_id or "").strip(),
             "qrcode": str(qrcode or "").strip()},
        )

    @action(description="确认 LOT 批次")
    def confirm_lot_batch(self, post_id: str = "", lot_id: str = "") -> ModbusActionResult:
        return self._business_call(
            "confirm_lot_batch",
            {"post_id": str(post_id or "").strip(), "lot_id": str(lot_id or "").strip()},
        )

    @action(description="扫描 LOT 到小坩埚")
    def scan_lot_to_small_cubic(
        self,
        post_id: str = "",
        lot_id: str = "",
        small_cubic_id: str = "",
        firing_type: int = 0,
    ) -> ModbusActionResult:
        return self._business_call(
            "scan_lot_to_small_cubic",
            {"post_id": str(post_id or "").strip(), "lot_id": str(lot_id or "").strip(),
             "small_cubic_id": str(small_cubic_id or "").strip(), "firing_type": int(firing_type)},
        )

    @action(description="完成 LOT 到小坩埚分配")
    def complete_lot_to_small_cubic(
        self, post_id: str = "", lot_id: str = "", small_cubic_id: str = ""
    ) -> ModbusActionResult:
        return self._business_call(
            "complete_lot_to_small_cubic",
            {"post_id": str(post_id or "").strip(), "lot_id": str(lot_id or "").strip(),
             "small_cubic_id": str(small_cubic_id or "").strip()},
        )

    @action(description="确认焦耳热下料计划")
    def confirm_joule_heating_schedule(
        self, post_id: str = "", joule_heating_fetch_mode: str = "auto"
    ) -> ModbusActionResult:
        mode = str(joule_heating_fetch_mode or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            raise ValueError('joule_heating_fetch_mode 必须是 "auto" 或 "manual"')
        return self._business_call(
            "confirm_joule_heating_schedule",
            {"post_id": str(post_id or "").strip(), "joule_heating_fetch_mode": mode},
        )

    @action(description="扫描瓶码并入库")
    def scan_bottle_to_stock(
        self, post_id: str = "", lot_id: str = "", bottle_code: str = ""
    ) -> ModbusActionResult:
        return self._business_call(
            "scan_bottle_to_stock",
            {"post_id": str(post_id or "").strip(), "lot_id": str(lot_id or "").strip(),
             "bottle_code": str(bottle_code or "").strip()},
        )

    @action(description="上传配方；名称需唯一")
    def upload_recipe(
        self,
        recipe_name: str,
        formula: str,
        synthesis_mass: float,
        n_ball_bead: int,
        powder_names: list[str] | None = None,
        powder_weights: list[float] | None = None,
        powder_tolerances: list[float] | None = None,
        powder_pre_adds: list[bool] | None = None,
    ) -> ModbusActionResult:
        materials = resolve_recipe_materials_input(
            powder_names, powder_weights, powder_tolerances, powder_pre_adds
        )
        param = flatten_recipe_param(
            recipe_name, formula, synthesis_mass, n_ball_bead, materials
        )
        if not param["recipe_name"]:
            raise ValueError("recipe_name 不能为空")
        return self._business_call("upload_recipe", param)

    @action(description="创建合成 TASK")
    def create_task(
        self,
        pallet_type: int = 1,
        cubic_type: int = 1,
        task_slot_nums: list[int] | None = None,
        task_recipe_names: list[str] | None = None,
        has_bead_bottle: bool = True,
        bead_count: int = 100,
    ) -> ModbusActionResult:
        slots = resolve_task_slots_input(task_slot_nums, task_recipe_names)
        return self._business_call(
            "create_task",
            {
                "pallet_type": int(pallet_type),
                "cubic_type": int(cubic_type),
                "has_bead_bottle": bool(has_bead_bottle),
                "bead_count": int(bead_count),
                "slots": slots,
            },
        )

    @action(description="启动 TASK")
    def start_task(self, task_id: str = "") -> ModbusActionResult:
        return self._business_call("start_task", self._business_task_param(task_id))

    @action(description="上坩埚")
    def upload_cubic(self, task_id: str = "", fetch_cubic_source: int = 1) -> ModbusActionResult:
        source = int(fetch_cubic_source)
        if source not in {0, 1}:
            raise ValueError("fetch_cubic_source 必须是 0 或 1")
        return self._business_call(
            "upload_cubic", self._business_task_param(task_id, {"fetch_cubic_source": source})
        )

    @action(always_free=True, description="查询上坩埚状态")
    def query_upload_cubic_status(self, task_id: str = "") -> ModbusActionResult:
        return self._business_call(
            "query_upload_cubic_status", self._business_task_param(task_id)
        )

    @action(description="加样确认：写入预加料实际重量")
    def confirm_recipe(
        self,
        task_id: str = "",
        slot_num: int = 1,
        material: str = "",
        real_weight: float = 0.0,
    ) -> ModbusActionResult:
        material_name = str(material or "").strip()
        if not material_name:
            raise ValueError("material 不能为空")
        return self._business_call(
            "confirm_recipe",
            self._business_task_param(
                task_id,
                {"slot_num": int(slot_num), "material": material_name, "real_weight": float(real_weight)},
            ),
        )

    @action(description="启动加样")
    def start_recipt(self, task_id: str = "") -> ModbusActionResult:
        return self._business_call("start_recipt", self._business_task_param(task_id))

    @action(always_free=True, description="查询加样状态")
    def get_recipt_status(self, task_id: str = "") -> ModbusActionResult:
        return self._business_call("get_recipt_status", self._business_task_param(task_id))

    @action(description="启动一次扫码绑定、称粉和加珠任务")
    def start_sampling(
        self,
        task_id: str = "",
        slot_num: int = 1,
        destination_slot: int | None = None,
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
            destination_slot=destination_slot,
            rack_positions=resolved_rack_positions,
            masses=resolved_masses,
            tolerances=resolved_tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
        )
        code = self._status_code(response.get("status", {}), "sample_add_powder_and_beads")
        accepted = bool(response.get("accepted", False)) and code != 3
        return {
            "accepted": accepted,
            "success": accepted,
            "message": "" if accepted else "PLC 拒绝称粉任务",
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
        destination_slot: int | None = None,
        rack_positions: list[int] | None = None,
        masses: list[float] | None = None,
        tolerances: list[float] | None = None,
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: list[str] | None = None,
        timeout: float = 30.0,
        step: float = 0.1,
        expected_crucible_id: str | None = None,
    ) -> SamplingCompletedResult:
        self._ensure_connected()
        resolved_rack_positions = rack_positions if rack_positions is not None else [1]
        resolved_masses = masses if masses is not None else [0.9]
        resolved_tolerances = tolerances if tolerances is not None else [0.0007]
        response = self.controller.run_sampling(
            task_id=task_id,
            slot_num=slot_num,
            destination_slot=destination_slot,
            rack_positions=resolved_rack_positions,
            masses=resolved_masses,
            tolerances=resolved_tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
            expected_crucible_id=expected_crucible_id,
            timeout=timeout,
            step=step,
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        weights = result.get("weights", {})
        result_codes = result.get("results", {})
        code = self._status_code(response.get("status", {}), "sample_add_powder_and_beads")
        accepted = bool(response.get("accepted", False)) and code != 3
        return {
            "accepted": accepted,
            "success": accepted,
            "message": "" if accepted else "PLC 称粉任务失败",
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

    @action(description="使用指定坩埚 ResourceSlot 执行扫码绑定和称粉")
    def sample_with_materials(
        self,
        crucible: Annotated[ResourceSlot, AllowedResourceTemplates(SynthesisCrucible)],
        source_site: str,
        task_id: str = "",
        slot_num: int = 1,
        destination_slot: int | None = None,
        rack_positions: list[int] | None = None,
        masses: list[float] | None = None,
        tolerances: list[float] | None = None,
        cubic_type: int = 1,
        bead_count: int = 0,
        from_outside: bool = False,
        material_names: list[str] | None = None,
        timeout: float = 30.0,
        step: float = 0.1,
    ) -> MaterialSamplingResult:
        mapped_slot = self._slot_from_site(source_site)
        if mapped_slot is not None and int(slot_num) != mapped_slot:
            raise ValueError(
                f"source_site {source_site!r} 对应 PLC 槽位 {mapped_slot}，"
                f"但收到 slot_num={slot_num}"
            )
        expected_id = self._resource_identity(crucible)
        result = self.sample(
            task_id=task_id,
            slot_num=slot_num,
            destination_slot=destination_slot,
            rack_positions=rack_positions,
            masses=masses,
            tolerances=tolerances,
            cubic_type=cubic_type,
            bead_count=bead_count,
            from_outside=from_outside,
            material_names=material_names,
            timeout=timeout,
            step=step,
            expected_crucible_id=expected_id,
        )
        return {
            **result,
            "success": result["accepted"] and result["status_name"] != "FAULT",
            "crucible": crucible,
            "source_site": str(source_site),
            "message": (
                f"坩埚扫码称粉完成：{result['qr_code']}"
                if result["accepted"]
                else "坩埚扫码称粉未接受"
            ),
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
        """取坩埚/托盘；``source`` 使用 IO 表中的 PLC 位置编号。"""

        self._ensure_connected()
        return self._command_result(self.controller.fetch_cubic(
            source=source,
            destination=destination,
            pallet_type=pallet_type,
            slot_numbers=slot_numbers or [],
            bead_source=bead_source,
        ))

    @action(description="按设备包来源编号取大坩埚")
    def fetch_cubic_from_package(
        self,
        source: int = 0,
        destination: int = 1,
        pallet_type: int = 1,
        slot_numbers: list[int] | None = None,
        bead_source: int = 0,
    ) -> CommandResult:
        """将设备包来源 0/1 映射为 PLC 来源 1/2 后取坩埚。"""

        self._ensure_connected()
        return self._command_result(self.controller.fetch_cubic_from_package(
            source=source,
            destination=destination,
            pallet_type=pallet_type,
            slot_numbers=slot_numbers or [],
            bead_source=bead_source,
        ))

    def _poll_plc_status(
        self,
        status_key: str,
        operation: str,
        timeout: float,
        step: float,
        previous: int,
    ) -> int:
        """Poll one PLC status word until it completes, faults, or times out.

        ``previous`` is the status sampled before the command write, so a
        leftover completion code cannot be mistaken for this command.
        """

        if timeout <= 0 or step <= 0:
            raise ValueError("timeout 和 step 必须为正数")
        saw_running = False
        deadline = time.monotonic() + float(timeout)
        while True:
            code = int(self.controller.status().get(status_key, 0) or 0)
            if code == 1:
                saw_running = True
            if code == 2 and (saw_running or previous != 2):
                return code
            if code == 3:
                raise RuntimeError(f"PLC{operation}报告故障状态")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待{operation}超时；最后状态={code}")
            if callable(getattr(self.controller.transport, "advance", None)):
                self.controller.advance(step)
            else:
                time.sleep(step)

    @staticmethod
    def _bead_count_for_command(has_bead_bottle: bool, bead_count: int) -> int:
        count = int(bead_count)
        if has_bead_bottle and count <= 0:
            raise ValueError("有加珠瓶时球磨珠数量必须大于 0")
        if count < 0 or count > 0xFFFF:
            raise ValueError("球磨珠数量必须是 0..65535 的整数")
        return count if has_bead_bottle else 0

    @action(
        node_type=NodeType.MANUAL_CONFIRM,
        displayname="确认后执行上托盘",
        description=(
            "操作员确认托盘和坩埚已放在料架2后，向 PLC 写入命令7，"
            "把托盘搬运到料架1。只写 Modbus 上托盘寄存器，不调用配方或 TASK 接口。"
        ),
    )
    def load_pallet_from_rack2(
        self,
        pallet_type: str = "6 槽位托盘",
        cubic_type: str = "Al2O3 30*30",
        task_slot_nums: list[int] | None = None,
        has_bead_bottle: bool = False,
        bead_count: int = 0,
        timeout: float = 600.0,
    ) -> CommandResult:
        """Write PLC command 7 after the operator confirms rack-2 loading."""

        self._ensure_connected()
        slots = [int(value) for value in (task_slot_nums or [])]
        slot_types = derive_pallet_slot_types(pallet_type, cubic_type, slots)
        beads = self._bead_count_for_command(has_bead_bottle, bead_count)
        previous = int(self.controller.status().get("upper_pallet", 0) or 0)
        self.controller.fetch_cubic_from_package(
            source=1,
            destination=1,
            pallet_type=pallet_type_code(pallet_type),
            slot_numbers=slot_types,
            bead_source=cubic_source_to_plc(1) if beads else 0,
        )
        code = self._poll_plc_status(
            "upper_pallet", "命令7上托盘", timeout, 0.2, previous
        )
        return {
            "accepted": True,
            "success": code == 2,
            "message": "PLC 命令7已完成，托盘已到料架1",
            "command": "fetch_cubic",
            "status_code": code,
            "status_name": self._status_name(code),
        }

    @action(
        displayname="料架注粉称重",
        description=(
            "向 PLC 写入命令3，由 PLC 完成扫码、天平开关门、称粉/加珠，"
            "并按所选槽位放回坩埚。只写 Modbus 加样寄存器，不调用配方或 TASK 接口。"
        ),
    )
    def dose_selected_slots(
        self,
        recipe_name: str,
        pallet_type: str = "6 槽位托盘",
        cubic_type: str = "Al2O3 30*30",
        task_slot_nums: list[int] | None = None,
        has_bead_bottle: bool = False,
        bead_count: int = 0,
        timeout: float = 1800.0,
    ) -> Rack2DoseResult:
        """Write one PLC command 3 for each selected tray slot."""

        self._ensure_connected()
        slots = [int(value) for value in (task_slot_nums or [])]
        derive_pallet_slot_types(pallet_type, cubic_type, slots)
        materials = [
            item for item in recipe_spec(recipe_name)["materials"] if not item["pre_add"]
        ]
        if not materials:
            raise ValueError("配方没有需要由 PLC 命令3执行的非预加粉料")
        inventory = rack_material_positions()
        positions: list[int] = []
        names: list[str] = []
        masses: list[float] = []
        tolerances: list[float] = []
        for item in materials:
            name = str(item["name"])
            if name not in inventory:
                raise ValueError(f"料架库存没有物料 {name} 的加样位置，不能下发命令3")
            positions.append(int(inventory[name]))
            names.append(name)
            masses.append(float(item["weight"]))
            tolerances.append(float(item["tolerance"]))
        beads = self._bead_count_for_command(has_bead_bottle, bead_count)
        cubic_code = cubic_type_code(cubic_type)
        qr_codes: list[str] = []
        weights: list[float] = []
        for slot in slots:
            sample = self.controller.run_sampling(
                slot_num=slot,
                destination_slot=slot,
                rack_positions=positions,
                masses=masses,
                tolerances=tolerances,
                cubic_type=cubic_code,
                bead_count=beads,
                from_outside=False,
                material_names=names,
                timeout=timeout,
                step=0.2,
            )
            result = sample.get("result") if isinstance(sample.get("result"), dict) else {}
            qr_codes.append(str(result.get("qr_code", "")))
            raw_weights = result.get("weights", {})
            if isinstance(raw_weights, dict):
                weights.extend(float(value) for value in raw_weights.values())
            else:
                weights.extend(float(value) for value in raw_weights)
        return {
            "success": True,
            "message": f"PLC 命令3已完成，共 {len(slots)} 个槽位",
            "command": "sample_add_powder_and_beads",
            "slot_nums": slots,
            "qr_codes": qr_codes,
            "weights": weights,
        }

    @staticmethod
    def _resonance_segment(
        acceleration: int, frequency: int, duration_minutes: int
    ) -> tuple[list[int], list[int], list[int]]:
        """One PLC command-8 segment.

        The operator enters time in minutes. Command 8 stores that time in
        seconds, and the seconds value must still fit in one 16-bit register.
        """

        accel = int(acceleration)
        freq = int(frequency)
        minutes = int(duration_minutes)
        if not 0 <= accel <= 130:
            raise ValueError("声共振加速度必须在 0–130")
        if not 0 <= freq <= 100:
            raise ValueError("声共振频率必须在 0–100")
        if not 1 <= minutes <= 1092:
            raise ValueError("声共振时间必须在 1–1092 分钟，下发前会换算成秒")
        seconds = minutes * 60
        return [accel], [freq], [seconds]

    @action(
        displayname="声共振上料并等待振动完成",
        description=(
            "向 PLC 写入命令8。取托盘位置为料架1，放托盘位置按既有编码为声共振工位。"
            "上料状态和声共振运行状态都完成后才返回，此时还没有下料。"
        ),
    )
    def run_acoustic_load(
        self,
        acceleration: int = 0,
        frequency: int = 0,
        duration_minutes: int = 0,
        timeout: float = 14400.0,
    ) -> CommandResult:
        """Write PLC command 8 and wait until the vibration itself finishes."""

        self._ensure_connected()
        accelerations, frequencies, times = self._resonance_segment(
            acceleration, frequency, duration_minutes
        )
        previous_load = int(self.controller.status().get("acoustic_resonance", 0) or 0)
        previous_process = int(self.controller.status().get("acoustic_process", 0) or 0)
        self.controller.acoustic_resonance(
            fetch_position=1,
            accelerations=accelerations,
            frequencies=frequencies,
            times=times,
        )
        self._poll_plc_status(
            "acoustic_resonance", "命令8声共振上料", timeout, 0.2, previous_load
        )
        code = self._poll_plc_status(
            "acoustic_process", "声共振运行", timeout, 0.2, previous_process
        )
        return {
            "accepted": True,
            "success": code == 2,
            "message": "声共振已振完，等待下料",
            "command": "acoustic_resonance",
            "status_code": code,
            "status_name": self._status_name(code),
        }

    @action(
        displayname="声共振下料",
        description=(
            "声共振运行完成后向 PLC 写入命令9，把托盘从声共振工位取出。"
            "命令9按既有编码只写命令号和取放位置，不再重发加速度、频率和时间。"
        ),
    )
    def run_acoustic_unload(self, timeout: float = 600.0) -> CommandResult:
        """Write PLC command 9 and wait for the unload status."""

        self._ensure_connected()
        previous = int(self.controller.status().get("acoustic_fetch", 0) or 0)
        self.controller.fetch_acoustic_resonance()
        code = self._poll_plc_status(
            "acoustic_fetch", "命令9声共振下料", timeout, 0.2, previous
        )
        return {
            "accepted": True,
            "success": code == 2,
            "message": "PLC 命令9已完成，声共振托盘已取出",
            "command": "fetch_acoustic_resonance",
            "status_code": code,
            "status_name": self._status_name(code),
        }

    @action(description="向坩埚加入研磨珠")
    def add_bead(self, source: int = 1) -> CommandResult:
        self._ensure_connected()
        return self._command_result(self.controller.add_bead(source=source))

    @action(description="下发声共振工艺")
    def start_acoustic_resonance(
        self,
        task_id: str = "",
        fetch_position: int = 1,
        accelerations: list[int] | None = None,
        frequencies: list[int] | None = None,
        times: list[int] | None = None,
    ) -> CommandResult:
        # A non-empty TASK ID selects the business simulator contract.  With
        # no ID this remains the raw Modbus command for direct PLC users.
        if str(task_id or "").strip():
            return self._business_call(
                "start_acoustic_resonance", self._business_task_param(task_id)
            )  # type: ignore[return-value]
        self._ensure_connected()
        return self._command_result(self.controller.acoustic_resonance(
            fetch_position=fetch_position,
            accelerations=accelerations or [],
            frequencies=frequencies or [],
            times=times or [],
        ))

    @action(always_free=True, description="取出声共振载具")
    def fetch_acoustic_resonance(self, task_id: str = "") -> CommandResult:
        if str(task_id or "").strip():
            return self._business_call(
                "fetch_acoustic_resonance", self._business_task_param(task_id)
            )  # type: ignore[return-value]
        self._ensure_connected()
        return self._command_result(self.controller.fetch_acoustic_resonance())

    @action(always_free=True, description="查询声共振上料、下料和运行状态")
    def get_acoustic_resonance_status(self, task_id: str = "") -> CommandResult:
        if str(task_id or "").strip():
            return self._business_call(
                "get_acoustic_resonance_status", self._business_task_param(task_id)
            )  # type: ignore[return-value]
        self._ensure_connected()
        snapshot = self.controller.status()
        return self._command_result({
            "accepted": True,
            "command": "get_acoustic_resonance_status",
            "status": snapshot,
        })

    @action(description="结束声共振工艺并进入后续装瓶")
    def finish_acoustic_resonance(self, task_id: str = "") -> CommandResult:
        """The IO table has no separate finish register.

        Command 9 performs physical unloading; this action records the
        supervisory boundary without inventing a PLC register write.
        """
        if str(task_id or "").strip():
            return self._business_call(
                "finish_acoustic_resonance", self._business_task_param(task_id)
            )  # type: ignore[return-value]
        self._ensure_connected()
        return self._command_result({
            "accepted": True,
            "command": "finish_acoustic_resonance",
            "status": self.controller.status(),
        })

    @action(description="扫码装瓶")
    def scan_big_cubic_to_bottle(self, task_id: str = "", qrcode: str = "") -> ModbusActionResult:
        code = str(qrcode or "").strip()
        if not code:
            raise ValueError("qrcode 不能为空")
        return self._business_call(
            "scan_big_cubic_to_bottle",
            self._business_task_param(task_id, {"qrcode": code}),
        )

    @action(description="启动烧结")
    def start_sintering(
        self, post_id: str = "", joule_heating_fetch_mode: str = "auto"
    ) -> ModbusActionResult:
        mode = str(joule_heating_fetch_mode or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            raise ValueError('joule_heating_fetch_mode 必须是 "auto" 或 "manual"')
        return self._business_call(
            "start_sintering", self._business_post_param(post_id, {"joule_heating_fetch_mode": mode})
        )

    @action(always_free=True, description="查询烧结状态")
    def get_sintering_status(self, post_id: str = "") -> ModbusActionResult:
        return self._business_call(
            "get_sintering_status", self._business_post_param(post_id)
        )

    @action(description="焦耳热下料")
    def fetch_joule_heating(self, post_id: str = "") -> ModbusActionResult:
        return self._business_call(
            "fetch_joule_heating", self._business_post_param(post_id)
        )

    @action(description="马弗炉下料")
    def fetch_furnace(self, post_id: str = "", furnace_id: int = 1) -> ModbusActionResult:
        furnace = int(furnace_id)
        if furnace not in {1, 2, 3, 4}:
            raise ValueError("furnace_id 必须是 1 到 4")
        return self._business_call(
            "fetch_furnace", self._business_post_param(post_id, {"furnace_id": furnace})
        )

    @action(description="关闭舱门")
    def close_cabin_outer_door(self, task_id: str = "") -> CommandResult:
        if str(task_id or "").strip():
            return self._business_call(
                "close_cabin_outer_door", self._business_task_param(task_id)
            )  # type: ignore[return-value]
        self._ensure_connected()
        return self._command_result(self.controller.close_cabin_door())

    @action(always_free=True, description="暂停 PLC 当前工艺")
    def pause(self) -> CommandResult:
        self._ensure_connected()
        result = self._command_result(self.controller.pause())
        pause_business = getattr(self.business_state, "pause", None)
        if callable(pause_business):
            pause_business()
        return result

    @action(always_free=True, description="恢复 PLC 当前工艺")
    def resume(self) -> CommandResult:
        self._ensure_connected()
        result = self._command_result(self.controller.resume())
        resume_business = getattr(self.business_state, "resume", None)
        if callable(resume_business):
            resume_business()
        return result

    @action(always_free=True, description="推进设备包内 PLC 仿真时钟")
    def advance_simulation(self, seconds: float = 0.1) -> StationStatusResult:
        if not self.simulation:
            raise RuntimeError("真实 PLC 模式不支持 advance_simulation")
        self._ensure_connected()
        self.controller.advance(seconds)
        advance_business = getattr(self.business_state, "advance", None)
        if callable(advance_business):
            advance_business(seconds)
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

    @action(always_free=True, description="清除设备包仿真故障并恢复空闲状态")
    def clear_fault(self, name: str = "") -> StationStatusResult:
        if not self.model:
            raise RuntimeError("真实 PLC 模式不支持仿真故障清除")
        self.controller.clear_fault(name or None)
        return self.station_status()

    @action(always_free=True, description="设置设备包 PLC 仿真互锁条件")
    def set_simulation_interlock(
        self, name: str, allowed: bool = True
    ) -> SimulationInterlockResult:
        """模拟 PLC 的门、急停、机器人和子设备就绪条件。"""

        if not self.model or not self.simulation:
            raise RuntimeError("真实 PLC 模式不支持仿真互锁设置")
        setter = getattr(self.controller.transport, "set_interlock", None)
        snapshotter = getattr(self.controller.transport, "interlock_snapshot", None)
        if not callable(setter) or not callable(snapshotter):
            raise RuntimeError("当前仿真传输不支持 PLC 互锁设置")
        setter(name, bool(allowed))
        snapshot = snapshotter()
        return {
            "name": str(name),
            "allowed": bool(allowed),
            "initialized": bool(snapshot.get("initialized", False)),
            "emergency_stop": bool(snapshot.get("emergency_stop", False)),
            "doors_closed": bool(snapshot.get("doors_closed", False)),
            "robot_ready": bool(snapshot.get("robot_ready", False)),
            "robot_auto": bool(snapshot.get("robot_auto", False)),
            "robot_safe": bool(snapshot.get("robot_safe", False)),
            "servo_ready": bool(snapshot.get("servo_ready", False)),
            "communications_ok": bool(snapshot.get("communications_ok", False)),
            "scanner_ready": bool(snapshot.get("scanner_ready", False)),
            "furnace_ready": bool(snapshot.get("furnace_ready", False)),
            "furnace_open_allowed": bool(snapshot.get("furnace_open_allowed", False)),
            "resonance_ready": bool(snapshot.get("resonance_ready", False)),
        }

    @action(always_free=True, description="重置设备包仿真 PLC 状态")
    def reset(self) -> StationStatusResult:
        if not self.model:
            raise RuntimeError("真实 PLC 模式不支持仿真 reset")
        self.controller.reset()
        reset_business = getattr(self.business_state, "reset", None)
        if callable(reset_business):
            reset_business()
        return self.station_status()


__all__ = ["YBSynthesisModbusStation"]
