"""YB 合成工站的 OS 原子动作层。

``YBSynthesisModbusStation`` 提供的是 PLC/上位机协议动作；本模块把这些
动作按不可中断的物理过程重新编排，并在 PLC/仿真反馈完成后提交一份
设备包侧物料位置账。默认使用设备包内的 Modbus 和业务仿真，不连接真实 PLC。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from unilabos.registry.annotations import AllowedResourceTemplates, JSONValue
from unilabos.registry.decorators import NodeType, action, device, not_action, topic_config
from unilabos.registry.placeholder_type import ResourceSlot

from yb_sse_devices.resources.synthesis_bead_bottle.resource import SynthesisBeadBottle
from yb_sse_devices.resources.synthesis_crucible.resource import SynthesisCrucible
from yb_sse_devices.resources.synthesis_powder.resource import SynthesisPowder
from yb_sse_devices.devices.yb_synthesis_modbus_station.device import YBSynthesisModbusStation
from yb_sse_devices.common.synthesis_protocol import (
    materials_from_columns,
    pallet_slot_count,
    resolve_task_slots_input,
)
from yb_sse_devices.common.synthesis_catalog import (
    cubic_type_code,
    default_recipe_inputs,
    derive_pallet_slot_types,
    pallet_type_code,
)
from yb_sse_devices.devices.yb_synthesis_modbus_station.modbus import cubic_source_to_plc


class AtomicResult(TypedDict):
    task_id: str
    post_id: str
    state: str
    message: str
    qrcode: str
    lot_id: str
    small_cubics: list[str]
    ledger_json: str
    # ResourceSlot 字段是工作流层的物料链；设备内部 _Ledger 仍负责
    # PLC 完成反馈后的库位审计。字段保持可选，兼容直接调用设备动作的旧接口。
    crucible: ResourceSlot | None
    small_crucible: ResourceSlot | None


class DoseRecipeResult(TypedDict):
    """称粉动作的完整 PLC 反馈结果。"""

    task_id: str
    post_id: str
    state: str
    message: str
    qrcode: str
    lot_id: str
    small_cubics: list[str]
    ledger_json: str
    crucible: ResourceSlot | None
    small_crucible: ResourceSlot | None
    sampling_results: list[dict[str, JSONValue]]


class PlcCommandResult(TypedDict):
    accepted: bool
    success: bool
    message: str
    command: str
    status_code: int
    status_name: str


class PlcDoseResult(TypedDict):
    success: bool
    message: str
    command: str
    slot_nums: list[int]
    qr_codes: list[str]
    weights: list[float]


class LegacyTaskActionResult(TypedDict):
    """Stable result returned by one-to-one legacy task action adapters."""

    task_id: str
    state: str
    message: str


class AtomicSamplingResult(TypedDict):
    """Stable result contract for the reusable PLC sampling boundary."""

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


class _Ledger:
    """原子移动账；动作失败时不会提前改写目标位置。"""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def put(self, item_id: str, *, kind: str, location: str, **metadata: Any) -> None:
        key = str(item_id).strip()
        if not key:
            raise ValueError("物料 ID 不能为空")
        if key in self.items:
            raise ValueError(f"物料已建账: {key}")
        self.items[key] = {"item_id": key, "kind": kind, "location": location, **metadata}

    def move(self, item_id: str, target: str, *, expected: str | None = None, **metadata: Any) -> None:
        key = str(item_id).strip()
        record = self.items.get(key)
        if record is None:
            raise ValueError(f"物料尚未建账: {key}")
        current = str(record.get("location") or "")
        if expected is not None and current != expected:
            raise RuntimeError(f"物料位置不匹配: {key} 当前={current!r}，期望={expected!r}")
        record.update(location=target, **metadata)

    def update(self, item_id: str, **metadata: Any) -> None:
        if item_id not in self.items:
            raise ValueError(f"物料尚未建账: {item_id}")
        self.items[item_id].update(metadata)

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(self.items[key]) for key in sorted(self.items)]

    def dumps(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)


@device(
    id="yb_synthesis_atomic_station",
    category=["workstation", "synthesis"],
    display_name="YB 合成工站（Modbus 原子动作）",
    description="直接使用 YB PLC Modbus 驱动，按完成握手提交物料位置账",
    icon="synthesis_station.webp",
    version="0.1.0",
)
class YBSynthesisAtomicStation:
    """面向 OS 的合成原子动作设备。"""

    def __init__(
        self,
        device_id: str | None = None,
        config: dict[str, Any] | None = None,
        action_timeout: float = 15.0,
        simulation_step: float = 0.1,
        **runtime_config: Any,
    ) -> None:
        resolved = dict(config or {})
        # Uni-Lab OS passes graph configuration as constructor keyword
        # arguments.  Preserve those values when this wrapper is created from
        # the deployment graph; otherwise the nested Modbus station silently
        # falls back to its in-process simulation defaults.
        for key, value in runtime_config.items():
            resolved.setdefault(key, value)
        resolved.setdefault("simulation", True)
        self.device_id = device_id or "yb_synthesis_atomic_station"
        self.action_timeout = max(0.1, float(resolved.get("action_timeout", action_timeout)))
        self.simulation_step = max(0.01, float(resolved.get("simulation_step", simulation_step)))
        self.station = YBSynthesisModbusStation(
            device_id=f"{self.device_id}_plc", config=resolved
        )
        # These are installation-specific PLC numbers.  They may be supplied
        # by an approved deployment configuration, but are intentionally not
        # exposed as workflow inputs.  Ignore absent values so an unconfirmed
        # real deployment still fails closed at batch/dose validation.
        self._configured_powder_rack_positions = self._configured_positions(
            resolved.get("powder_rack_positions")
        )
        self._configured_crucible_return_positions = self._configured_positions(
            resolved.get("crucible_return_positions")
        )
        self._lock = threading.RLock()
        self._ledger = _Ledger()
        self._task_id = ""
        self._post_id = ""
        self._task_slots: list[dict[str, Any]] = []
        self._materials: list[dict[str, Any]] = []
        self._local_batch = False
        self._powder_rack_positions: list[int] = []
        self._crucible_return_positions: list[int] = []
        self._pallet_type = 1
        self._cubic_type = 1
        self._fetch_cubic_source = 1
        self._recipe_name = ""
        self._bead_count = 0
        self._confirmed_weights: dict[str, float] = {}
        self._recipe_confirmed = False
        self._big_crucible_id = ""
        # ``sinter_and_unload`` is the terminal physical step of the current
        # workflow, but ``store_fired_material`` may still be called afterwards.
        # Keep the ledger available for that optional post-processing action;
        # the next batch may reinitialize it only after this terminal marker.
        self._batch_terminal = False
        self._resonance_started = False
        self._bottle_scanned = False
        self._last_qrcode = ""
        self._small_crucible_assigned = False
        self._assigned_small_code = ""
        self._sinter_started = False
        self._state = "IDLE"
        self._closed = False

    @staticmethod
    def _configured_positions(value: Any) -> list[int]:
        if not isinstance(value, (list, tuple)):
            return []
        if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            return []
        return [int(item) for item in value]

    @not_action
    def close(self) -> None:
        if not self._closed:
            self.station.close()
            self._closed = True

    @property
    @topic_config(period=1)
    def status(self) -> str:
        return "CLOSED" if self._closed else self._state

    @property
    @topic_config(period=1)
    def connected(self) -> bool:
        return bool(self.station.connected) and not self._closed

    @property
    @topic_config(period=1)
    def task_id(self) -> str:
        return self._task_id

    @property
    @topic_config(period=1)
    def post_id(self) -> str:
        return self._post_id

    def _result(self, state: str, message: str, **values: Any) -> AtomicResult:
        self._state = state
        # ResourceSlot 字段采用可空合同：旧的直接设备调用没有工作流物料
        # 参数时仍能返回合法结果；由原子工作流传入时则透传真实 ResourceSlot。
        values.setdefault("crucible", None)
        values.setdefault("small_crucible", None)
        return {
            "state": state,
            "message": message,
            "task_id": self._task_id,
            "post_id": self._post_id,
            "ledger_json": self._ledger.dumps(),
            **values,
        }

    @staticmethod
    def _ok(response: dict[str, Any]) -> bool:
        if not isinstance(response, dict):
            return False
        if "result" in response:
            try:
                return int(response.get("result", 1)) == 0
            except (TypeError, ValueError):
                return False
        return bool(response.get("success", response.get("accepted", False)))

    def _require_ok(self, response: dict[str, Any], operation: str) -> dict[str, Any]:
        if not self._ok(response):
            raise RuntimeError(f"{operation} 失败: {response}")
        return response

    def _wait(self, predicate: Any, operation: str) -> Any:
        deadline = time.monotonic() + self.action_timeout
        last: Any = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            # Raw PLC handshakes must not advance the separate TASK/POST
            # business clock; doing so can auto-create a POST before the
            # explicit scan-and-bottle boundary.  Advance only transports
            # that explicitly expose a deterministic clock (including the
            # direct-PLC test seam); a real Modbus socket is polled by sleep.
            if callable(getattr(self.station.controller.transport, "advance", None)):
                self.station.controller.advance(self.simulation_step)
            else:
                time.sleep(self.simulation_step)
        raise TimeoutError(f"等待{operation}超时；最后状态={last}")

    def _require_task(self, task_id: str = "") -> str:
        value = str(task_id or self._task_id).strip()
        if not value:
            raise ValueError("task_id 不能为空；请先 create_batch")
        if self._task_id and value != self._task_id:
            raise RuntimeError("原子动作不能交错执行多个 TASK")
        return value

    def _direct_plc_mode(self) -> bool:
        """Return whether the nested station has only the PLC contract.

        Recipe/TASK/POST actions are a separate business API.  A production
        Modbus graph deliberately has no business simulator, so the atomic
        layer must keep the task ledger locally and use the direct PLC
        commands instead of manufacturing business registers.
        """

        return self._local_batch or (
            not self.station.simulation and self.station.business_state is None
        )

    @staticmethod
    def _validate_direct_batch(
        materials: list[dict[str, Any]],
        slots: list[dict[str, Any]],
        powder_rack_positions: list[int] | None,
        pallet_type: int,
        pallet_slot_types: list[int] | None,
        has_bead_bottle: bool,
        bead_count: int,
        cubic_type: int,
    ) -> None:
        if powder_rack_positions is None:
            raise ValueError("真实 PLC 模式必须显式提供 powder_rack_positions")
        if len(powder_rack_positions) != len(materials):
            raise ValueError("powder_rack_positions 必须与 powder_names 一一对应")
        if any(
            not isinstance(value, int) or not 0 <= value <= 0xFFFF
            for value in powder_rack_positions
        ):
            raise ValueError("powder_rack_positions 必须是 0..65535 的整数")
        if not slots:
            raise ValueError("至少需要一个 task_slot_nums")
        pallet_slot_count(int(pallet_type))
        if pallet_slot_types is None or len(pallet_slot_types) != 6:
            raise ValueError("真实 PLC 模式必须显式提供 6 个 pallet_slot_types")
        if any(not isinstance(value, int) or not 0 <= value <= 0xFFFF for value in pallet_slot_types):
            raise ValueError("pallet_slot_types 必须是 0..65535 的整数")
        if not isinstance(cubic_type, int) or not 0 <= cubic_type <= 0xFFFF:
            raise ValueError("cubic_type 必须是无符号 16 位整数")
        if not isinstance(bead_count, int) or not 0 <= bead_count <= 0xFFFF:
            raise ValueError("bead_count 必须是无符号 16 位整数")
        if has_bead_bottle and bead_count <= 0:
            raise ValueError("has_bead_bottle=True 时 bead_count 必须大于 0")

    def _require_post(self, post_id: str = "") -> str:
        value = str(post_id or self._post_id).strip()
        if not value:
            raise ValueError("post_id 不能为空；请先 prepare_post")
        if self._post_id and value != self._post_id:
            raise RuntimeError("原子动作不能交错执行多个 POST")
        return value

    def _task_lots(self, task_id: str) -> list[dict[str, Any]]:
        response = self._require_ok(self.station.query_tasks(), "查询 TASK")
        for task in (response.get("data") or {}).get("tasks") or []:
            if str(task.get("task_id") or "") == task_id:
                return list(task.get("LOT") or [])
        raise RuntimeError(f"查询不到 TASK: {task_id}")

    def _post_payload(self, post_id: str) -> dict[str, Any]:
        response = self._require_ok(self.station.query_posts(), "查询 POST")
        for post in (response.get("data") or {}).get("posts") or []:
            if str(post.get("post_id") or "") == post_id:
                return post
        raise RuntimeError(f"查询不到 POST: {post_id}")

    def _legacy_task_result(
        self,
        task_id: str,
        state: str,
        message: str,
    ) -> LegacyTaskActionResult:
        """Record and return the stable boundary for a legacy task action."""

        if not self._task_id:
            self._task_id = task_id
        self._state = state
        return {"task_id": task_id, "state": state, "message": message}

    @action(description="兼容旧流程：从方舱或料架2上大坩埚")
    def upload_cubic(
        self,
        task_id: str = "",
        fetch_cubic_source: int = 1,
    ) -> LegacyTaskActionResult:
        with self._lock:
            resolved = self._require_task(task_id)
            source = int(fetch_cubic_source)
            if source not in {0, 1}:
                raise ValueError("fetch_cubic_source 必须是 0（方舱）或 1（料架2）")
            self._require_ok(
                self.station.upload_cubic(resolved, source),
                "旧流程上坩埚",
            )
            return self._legacy_task_result(
                resolved,
                "LEGACY_CUBIC_UPLOADED",
                "旧流程上坩埚命令已接受",
            )

    @action(description="兼容旧流程：人工确认后关闭方舱外门")
    def close_cabin_outer_door(
        self,
        task_id: str = "",
    ) -> LegacyTaskActionResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._require_ok(
                self.station.close_cabin_outer_door(resolved),
                "旧流程关闭方舱外门",
            )
            return self._legacy_task_result(
                resolved,
                "LEGACY_CABIN_CLOSED",
                "旧流程方舱外门已关闭",
            )

    @action(description="兼容旧流程：启动加粉加珠")
    def start_recipt(self, task_id: str = "") -> LegacyTaskActionResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._require_ok(
                self.station.start_recipt(resolved),
                "旧流程启动加粉加珠",
            )
            return self._legacy_task_result(
                resolved,
                "LEGACY_DOSING_STARTED",
                "旧流程加粉加珠已启动",
            )

    @action(description="兼容旧流程：启动声共振")
    def start_acoustic_resonance(
        self,
        task_id: str = "",
    ) -> LegacyTaskActionResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._require_ok(
                self.station.start_acoustic_resonance(resolved),
                "旧流程启动声共振",
            )
            return self._legacy_task_result(
                resolved,
                "LEGACY_RESONANCE_STARTED",
                "旧流程声共振已启动",
            )

    @action(description="兼容旧流程：声共振下料")
    def fetch_acoustic_resonance(
        self,
        task_id: str = "",
    ) -> LegacyTaskActionResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._require_ok(
                self.station.fetch_acoustic_resonance(resolved),
                "旧流程声共振下料",
            )
            return self._legacy_task_result(
                resolved,
                "LEGACY_RESONANCE_FETCHED",
                "旧流程声共振下料已完成",
            )

    @action(description="兼容旧流程：结束声共振")
    def finish_acoustic_resonance(
        self,
        task_id: str = "",
    ) -> LegacyTaskActionResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._require_ok(
                self.station.finish_acoustic_resonance(resolved),
                "旧流程结束声共振",
            )
            return self._legacy_task_result(
                resolved,
                "LEGACY_RESONANCE_FINISHED",
                "旧流程声共振已结束",
            )

    @action(description="创建合成批次并建立物料初始账；真实 PLC 由 OS 本地建账")
    def create_batch(
        self,
        recipe_name: str = "YB-SIM-Li6PS5Cl",
        formula: str | None = None,
        synthesis_mass: float | None = None,
        n_ball_bead: int | None = None,
        powder_names: list[str] | None = None,
        powder_weights: list[float] | None = None,
        powder_tolerances: list[float] | None = None,
        powder_pre_adds: list[bool] | None = None,
        # The registry's workflow-v1 schema accepts one scalar type here;
        # legacy callers may still pass integers at runtime and the catalog
        # resolver accepts both integer and label values.
        pallet_type: str = "1",
        cubic_type: str = "1",
        task_slot_nums: list[int] | None = None,
        has_bead_bottle: bool = True,
        bead_count: int = 80,
        fetch_cubic_source: int = 1,
        powder_rack_positions: list[int] | None = None,
        pallet_slot_types: list[int] | None = None,
    ) -> AtomicResult:
        with self._lock:
            # Resolve operator-facing catalog labels inside the device package
            # so the workflow never needs to expose PLC integer fields.
            recipe_defaults = default_recipe_inputs(recipe_name)
            formula = (
                recipe_defaults["formula"] if formula is None else str(formula)
            )
            synthesis_mass = (
                recipe_defaults["synthesis_mass"]
                if synthesis_mass is None
                else float(synthesis_mass)
            )
            n_ball_bead = (
                recipe_defaults["n_ball_bead"]
                if n_ball_bead is None
                else int(n_ball_bead)
            )
            resolved_pallet_type = pallet_type_code(pallet_type)
            resolved_cubic_type = cubic_type_code(cubic_type)
            # ``None`` powder columns mean "use the selected package recipe".
            # Explicit columns remain supported for legacy atomic workflows.
            if not powder_names:
                powder_names = recipe_defaults["powder_names"]
                powder_weights = recipe_defaults["powder_weights"]
                powder_tolerances = recipe_defaults["powder_tolerances"]
                powder_pre_adds = recipe_defaults["powder_pre_adds"]
            if self._task_id and self._batch_terminal:
                # A completed batch can remain queryable for
                # ``store_fired_material``.  Starting another batch must not
                # reuse that task, post, or ledger state.
                self._task_id = ""
                self._post_id = ""
                self._task_slots = []
                self._materials = []
                self._local_batch = False
                self._powder_rack_positions = []
                self._crucible_return_positions = []
                self._pallet_type = 1
                self._cubic_type = 1
                self._fetch_cubic_source = 1
                self._recipe_name = ""
                self._bead_count = 0
                self._confirmed_weights = {}
                self._recipe_confirmed = False
                self._big_crucible_id = ""
                self._ledger = _Ledger()
                self._batch_terminal = False
                self._resonance_started = False
                self._bottle_scanned = False
                self._last_qrcode = ""
                self._small_crucible_assigned = False
                self._assigned_small_code = ""
                self._sinter_started = False
            if self._task_id:
                raise RuntimeError("已有活动 TASK，不能交错创建第二个批次")
            local_batch = self._direct_plc_mode()
            if powder_rack_positions is None and self._configured_powder_rack_positions:
                powder_rack_positions = list(self._configured_powder_rack_positions)
            materials = materials_from_columns(
                powder_names, powder_weights, powder_tolerances, powder_pre_adds
            )
            slots = resolve_task_slots_input(
                task_slot_nums,
                [str(recipe_name)] * max(1, len(task_slot_nums or [1])),
            )
            slots = [{"slot_num": int(item["slot_num"]), "recipe_name": str(recipe_name)} for item in slots]
            if pallet_slot_types is None:
                pallet_slot_types = derive_pallet_slot_types(
                    pallet_type,
                    cubic_type,
                    [int(item["slot_num"]) for item in slots],
                )
            if fetch_cubic_source not in {0, 1}:
                raise ValueError("fetch_cubic_source 必须是 0（方舱）或 1（料架2）")
            if local_batch:
                self._validate_direct_batch(
                    materials, slots, powder_rack_positions,
                    resolved_pallet_type, pallet_slot_types,
                    has_bead_bottle, bead_count, resolved_cubic_type,
                )
                if fetch_cubic_source != 1:
                    raise ValueError("当前直接批次仅支持料架2来源；方舱关门需独立确认流程")
                # The real PLC IO table contains command/status registers but
                # no recipe/TASK business API.  Keep this identity entirely
                # on the OS side for tracing and resource accounting.
                task_id = f"OS-TASK-{uuid4().hex[:12].upper()}"
            else:
                recipe_response = self.station.upload_recipe(
                    recipe_name, formula, synthesis_mass, n_ball_bead,
                    powder_names=[str(item["name"]) for item in materials],
                    powder_weights=[float(item["weight"]) for item in materials],
                    powder_tolerances=[float(item["tolerance"]) for item in materials],
                    powder_pre_adds=[bool(item["pre_add"]) for item in materials],
                )
                if not self._ok(recipe_response):
                    # Recipe names are persistent PLC records.  Re-running a
                    # completed batch with the same recipe is valid, and the
                    # PLC reports the existing-name condition as result=4.
                    try:
                        duplicate_recipe = int(recipe_response.get("result", -1)) == 4
                    except (AttributeError, TypeError, ValueError):
                        duplicate_recipe = False
                    if not duplicate_recipe:
                        self._require_ok(recipe_response, "上传配方")
                created = self._require_ok(
                    self.station.create_task(
                        pallet_type=resolved_pallet_type,
                        cubic_type=resolved_cubic_type,
                        task_slot_nums=[int(item["slot_num"]) for item in slots],
                        task_recipe_names=[str(item["recipe_name"]) for item in slots],
                        has_bead_bottle=has_bead_bottle,
                        bead_count=bead_count,
                    ),
                    "创建 TASK",
                )
                task_id = str(created.get("task_id") or (created.get("data") or {}).get("task_id") or "")
                if not task_id:
                    raise RuntimeError(f"创建 TASK 未返回 task_id: {created}")
                self._require_ok(self.station.start_task(task_id), "启动 TASK")
            self._task_id = task_id
            self._local_batch = local_batch
            self._powder_rack_positions = list(powder_rack_positions or [])
            self._crucible_return_positions = []
            self._pallet_type = resolved_pallet_type
            self._cubic_type = resolved_cubic_type
            self._fetch_cubic_source = int(fetch_cubic_source)
            self._recipe_name = str(recipe_name)
            self._task_slots = slots
            self._materials = materials
            effective_bead_count = int(bead_count)
            if effective_bead_count == 0 and has_bead_bottle and n_ball_bead:
                effective_bead_count = int(n_ball_bead)
            self._bead_count = effective_bead_count if has_bead_bottle else 0
            self._big_crucible_id = (
                f"crucible:{task_id}" if local_batch
                else str(getattr(self.station, "simulation_crucible_id", "CRU-SIM-001"))
            )
            source = "cabin" if fetch_cubic_source == 0 else "crucible_rack_2"
            self._ledger.put(self._big_crucible_id, kind="big_crucible", location=source, task_id=task_id, cubic_type=resolved_cubic_type,
                             recipe_name=recipe_name, formula=formula, synthesis_mass=synthesis_mass)
            for index, material in enumerate(materials, start=1):
                self._ledger.put(
                    f"powder:{task_id}:{index}:{material['name']}",
                    kind="powder", location="powder_store", material=str(material["name"]),
                    quantity=float(material["weight"]), pre_add=bool(material["pre_add"]),
                )
            if has_bead_bottle and effective_bead_count > 0:
                self._ledger.put(f"beads:{task_id}", kind="ball_beads", location="bead_bottle", quantity=effective_bead_count)
            return self._result(
                "READY",
                "上位机批次已创建（PLC 直接模式）"
                if self._direct_plc_mode()
                else "TASK 已创建并启动",
            )

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
    ) -> PlcCommandResult:
        """Forward rack-2 loading to the nested PLC station."""

        return self.station.load_pallet_from_rack2(
            pallet_type=pallet_type,
            cubic_type=cubic_type,
            task_slot_nums=task_slot_nums,
            has_bead_bottle=has_bead_bottle,
            bead_count=bead_count,
            timeout=timeout,
        )

    @action(
        node_type=NodeType.MANUAL_CONFIRM,
        displayname="确认后执行注粉称重",
        description=(
            "操作员确认现场允许加样后，向 PLC 写入命令3，"
            "由 PLC 完成扫码、天平开关门、称粉/加珠，并按所选槽位放回坩埚。"
            "只写 Modbus 加样寄存器，不调用配方或 TASK 接口。"
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
    ) -> PlcDoseResult:
        """Forward rack-2 dosing to the nested PLC station."""

        return self.station.dose_selected_slots(
            recipe_name=recipe_name,
            pallet_type=pallet_type,
            cubic_type=cubic_type,
            task_slot_nums=task_slot_nums,
            has_bead_bottle=has_bead_bottle,
            bead_count=bead_count,
            timeout=timeout,
        )

    @action(
        node_type=NodeType.MANUAL_CONFIRM,
        displayname="确认后声共振上料",
        description=(
            "操作员确认命令3已结束、托盘仍在料架1后，向 PLC 写入命令8，"
            "并等待声共振运行完成。振动结束前不写下料命令。"
        ),
    )
    def run_acoustic_load(
        self,
        acceleration: int = 0,
        frequency: int = 0,
        duration_minutes: int = 0,
        timeout: float = 14400.0,
    ) -> PlcCommandResult:
        """Confirm, then run PLC command 8 until the vibration finishes."""

        return self.station.run_acoustic_load(
            acceleration=acceleration,
            frequency=frequency,
            duration_minutes=duration_minutes,
            timeout=timeout,
        )

    @action(
        displayname="声共振下料",
        description="声共振运行完成后向 PLC 写入命令9，取出托盘。",
    )
    def run_acoustic_unload(self, timeout: float = 600.0) -> PlcCommandResult:
        """Run PLC command 9 after the vibration status is complete."""

        return self.station.run_acoustic_unload(timeout=timeout)

    @action(
        node_type=NodeType.MANUAL_CONFIRM,
        displayname="确认后放入焦耳热并加热",
        description=(
            "操作员确认声共振已经下料，并且小坩埚已人工加料完成后，"
            "机械臂把小坩埚放入焦耳热，按所选载体和加热模式加热。"
        ),
    )
    def run_joule_heating(
        self,
        carrier_type: str = "椭圆石墨舟",
        heating_mode: str = "恒温",
        pickup_positions: list[int] | None = None,
        constant_temperature: int = 0,
        constant_hold_minutes: int = 0,
        slope_segments: str = "[]",
        timeout: float = 14400.0,
    ) -> PlcCommandResult:
        """Confirm, then run PLC command 4 until Joule heating finishes."""

        return self.station.run_joule_heating(
            carrier_type=carrier_type,
            heating_mode=heating_mode,
            pickup_positions=pickup_positions,
            constant_temperature=constant_temperature,
            constant_hold_minutes=constant_hold_minutes,
            slope_segments=slope_segments,
            timeout=timeout,
        )

    @action(
        node_type=NodeType.MANUAL_CONFIRM,
        displayname="等待一分钟后焦耳热下料",
        description="加热完成后，操作员等待一分钟再确认，然后从焦耳热取出小坩埚。",
    )
    def unload_joule_heating(
        self,
        carrier_type: str = "椭圆石墨舟",
        pickup_positions: list[int] | None = None,
        timeout: float = 600.0,
    ) -> PlcCommandResult:
        """Confirm after one minute, then run PLC command 5."""

        return self.station.unload_joule_heating(
            carrier_type=carrier_type,
            pickup_positions=pickup_positions,
            timeout=timeout,
        )

    @action(description="取大坩埚（PLC命令7含取盖/放盖）并完成方舱关门握手")
    def load_big_crucible(
        self,
        task_id: str = "",
        fetch_cubic_source: int | None = None,
        destination: int = 1,
        pallet_type: str | None = None,
        pallet_slot_types: list[int] | None = None,
        bead_source: int | None = None,
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            source = fetch_cubic_source
            if source is None:
                source = 0 if self._ledger.items[self._big_crucible_id]["location"] == "cabin" else 1
            if int(source) not in {0, 1}:
                raise ValueError("fetch_cubic_source 必须是 0（方舱）或 1（料架2）")
            resolved_pallet_type = self._pallet_type if pallet_type is None else pallet_type_code(pallet_type)
            if pallet_slot_types is None:
                pallet_slot_types = derive_pallet_slot_types(
                    resolved_pallet_type,
                    self._cubic_type,
                    [int(item["slot_num"]) for item in self._task_slots],
                )
            if self._direct_plc_mode():
                if len(pallet_slot_types) != 6:
                    raise ValueError("pallet_slot_types 必须是 6 个槽位类型")
                resolved_bead_source = (
                    int(bead_source)
                    if bead_source is not None
                    else (cubic_source_to_plc(int(source)) if self._bead_count else 0)
                )
                self._require_ok(
                    self.station.fetch_cubic_from_package(
                        source=int(source),
                        destination=int(destination),
                        pallet_type=int(resolved_pallet_type),
                        slot_numbers=list(pallet_slot_types),
                        bead_source=resolved_bead_source,
                    ),
                    "PLC 命令7上坩埚",
                )
                if int(source) == 0:
                    # The cabin-door handshake is a raw PLC command when no
                    # business TASK ID is supplied.
                    self._require_ok(
                        self.station.close_cabin_outer_door(),
                        "关闭方舱外门",
                    )
                self._wait(
                    lambda: self._plc_operation_complete("upper_pallet", "上坩埚"),
                    "上坩埚完成",
                )
            else:
                self._require_ok(self.station.upload_cubic(resolved, int(source)), "上坩埚")
                if int(source) == 0:
                    self._require_ok(self.station.close_cabin_outer_door(resolved), "关闭方舱外门")
                self._wait(
                    lambda: int((self.station.query_upload_cubic_status(resolved).get("data") or {}).get("fetch_cubic_state") or 0) == 2,
                    "上坩埚完成",
                )
            expected = "cabin" if int(source) == 0 else "crucible_rack_2"
            self._ledger.move(self._big_crucible_id, "synthesis_chamber", expected=expected)
            values: dict[str, Any] = {}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("LOADED", "大坩埚已到合成位", **values)

    def _plc_operation_complete(self, status_key: str, operation: str) -> bool:
        """Poll one direct PLC status register and fail on the PLC fault code."""

        snapshot = self.station.controller.status()
        code = int(snapshot.get(status_key, 0) or 0)
        if code == 3:
            raise RuntimeError(f"PLC{operation}报告故障状态")
        return code == 2

    def _parse_real_weights(self, real_weights_json: str) -> dict[str, float]:
        if not real_weights_json.strip():
            return {}
        raw = json.loads(real_weights_json)
        if not isinstance(raw, dict):
            raise ValueError("real_weights_json 必须是 {物料名: 实际重量} 对象")
        return {str(key): float(value) for key, value in raw.items()}

    def _confirm_recipe_impl(self, resolved: str, weights: dict[str, float]) -> None:
        if not self._direct_plc_mode():
            for slot in self._task_slots:
                for material in self._materials:
                    if material.get("pre_add"):
                        self._require_ok(
                            self.station.confirm_recipe(
                                resolved,
                                int(slot["slot_num"]),
                                str(material["name"]),
                                float(weights.get(str(material["name"]), material["weight"])),
                            ),
                            "确认预加料",
                        )
        self._confirmed_weights = dict(weights)
        self._recipe_confirmed = True

    @action(description="确认预加料配方和实际重量")
    def confirm_recipe(
        self,
        task_id: str = "",
        real_weights_json: str = "",
    ) -> AtomicResult:
        """在启动 CMD_SAMPLE 前确认需要人工复核的预加料重量。

        PLC 的 CMD_SAMPLE 会把内部扫码、称粉和加珠作为一个连续流程；这个
        节点只负责上位机配方确认，不能冒充 PLC 的扫码节点。
        """

        with self._lock:
            resolved = self._require_task(task_id)
            self._confirm_recipe_impl(resolved, self._parse_real_weights(real_weights_json))
            return self._result("RECIPE_CONFIRMED", "预加料配方已确认")

    def _dose_recipe_impl(
        self,
        task_id: str = "",
        real_weights_json: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
        powder_1: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        powder_2: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        powder_3: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        powder_4: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        bead_bottle: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisBeadBottle)
        ] = None,
        powder_rack_positions: list[int] | None = None,
        crucible_return_positions: list[int] | None = None,
        crucible_return_location: str = "synthesis_crucible_rack_01",
    ) -> DoseRecipeResult:
        """执行 PLC 命令3的完整复合动作。

        命令3内部负责天平开门、放入坩埚、关门称重、称重完成后再次开门
        取出坩埚、关门，并按 ``crucible_return_positions`` 放回目标位置；
        OS 不另发一个“天平开盖”命令，避免把 PLC 内部步骤拆成错误的外部动作。
        """
        resolved = self._require_task(task_id)
        direct_mode = self._direct_plc_mode()
        if crucible_return_positions is None:
            if self._crucible_return_positions:
                resolved_return_positions = list(self._crucible_return_positions)
            elif self._configured_crucible_return_positions:
                resolved_return_positions = list(self._configured_crucible_return_positions)
            elif direct_mode:
                raise ValueError(
                    "真实 PLC 模式未配置 crucible_return_positions；"
                    "这是命令3的放回位置，不能用任务槽位猜测"
                )
            else:
                resolved_return_positions = [int(slot["slot_num"]) for slot in self._task_slots]
        else:
            resolved_return_positions = [int(value) for value in crucible_return_positions]
            # Explicit values are retained only for backwards-compatible
            # direct callers.  The operator workflow never exposes this PLC
            # mapping; a real deployment must provide it through station
            # configuration once the installation is confirmed.
            self._crucible_return_positions = list(resolved_return_positions)
        if len(resolved_return_positions) != len(self._task_slots):
            raise ValueError("crucible_return_positions 必须与 task_slot_nums 一一对应")
        if any(not 0 <= value <= 0xFFFF for value in resolved_return_positions):
            raise ValueError("crucible_return_positions 必须是 0..65535 的整数")
        resolved_return_location = str(crucible_return_location).strip()
        if not resolved_return_location:
            raise ValueError("crucible_return_location 不能为空")
        weights = self._parse_real_weights(real_weights_json)
        if not self._recipe_confirmed:
            self._confirm_recipe_impl(resolved, weights)
        elif weights:
            # A direct caller may still provide the actual weights at this
            # boundary; use them without sending a duplicate confirmation.
            self._confirmed_weights.update(weights)
        if not direct_mode:
            self._require_ok(self.station.start_recipt(resolved), "启动加样")
        sampling_results: list[dict[str, Any]] = []
        for slot, return_position in zip(self._task_slots, resolved_return_positions):
            active_materials = [
                item for item in self._materials if not bool(item.get("pre_add"))
            ]
            names = [str(item["name"]) for item in active_materials]
            if not names:
                raise ValueError("没有需要由 PLC 命令3执行的非预加粉料")
            if powder_rack_positions is None:
                if self._direct_plc_mode():
                    all_rack_positions = list(self._powder_rack_positions)
                    if not all_rack_positions:
                        raise ValueError(
                            "真实 PLC 模式必须显式提供 powder_rack_positions"
                        )
                else:
                    all_rack_positions = list(range(1, len(self._materials) + 1))
            else:
                all_rack_positions = [int(value) for value in powder_rack_positions]
            if len(all_rack_positions) != len(self._materials):
                raise ValueError(
                    "powder_rack_positions 必须与 powder_names 一一对应"
                )
            active_indices = [
                index for index, item in enumerate(self._materials)
                if not bool(item.get("pre_add"))
            ]
            resolved_rack_positions = [all_rack_positions[index] for index in active_indices]
            sample = self.station.sample(
                task_id=resolved,
                slot_num=int(slot["slot_num"]),
                destination_slot=int(return_position),
                rack_positions=resolved_rack_positions,
                masses=[float(item["weight"]) for item in active_materials],
                tolerances=[float(item["tolerance"]) for item in active_materials],
                cubic_type=int(self._ledger.items[self._big_crucible_id].get("cubic_type", 1)),
                # CMD_SAMPLE is the confirmed PLC composite: internal scan,
                # powder dosing and ball-bead loading happen in this one
                # continuous physical action.
                bead_count=int(self._bead_count),
                material_names=names,
                expected_crucible_id=self._big_crucible_id,
                timeout=self.action_timeout,
                step=self.simulation_step,
            )
            if not sample.get("success", sample.get("accepted", False)):
                raise RuntimeError(f"称粉失败: {sample}")
            sampling_results.append(sample)
        for item_id, record in list(self._ledger.items.items()):
            if record.get("kind") == "powder" and record.get("location") == "powder_store":
                self._ledger.move(
                    item_id,
                    self._big_crucible_id,
                    expected="powder_store",
                    actual_weight=self._confirmed_weights.get(str(record["material"]), record["quantity"]),
                )
        bead_id = f"beads:{resolved}"
        if bead_id in self._ledger.items and self._bead_count > 0:
            self._ledger.move(bead_id, self._big_crucible_id, expected="bead_bottle")
        self._ledger.update(
            self._big_crucible_id,
            location=resolved_return_location,
            contents="recipe_powder_and_ball_beads",
            synthesis_state="dosed",
            plc_return_positions=resolved_return_positions,
            balance_door_cycle_completed=True,
        )
        values: dict[str, Any] = {"sampling_results": sampling_results}
        if crucible is not None:
            values["crucible"] = crucible
        return self._result("DOSED", "内部扫码、称粉和加珠已完成", **values)

    @action(description="内部扫码、天平开关门、称粉、取回并放回坩埚（PLC CMD_SAMPLE 连续动作）")
    def dose_recipe(
        self,
        task_id: str = "",
        real_weights_json: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
        powder_1: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        powder_2: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        powder_3: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        powder_4: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisPowder)
        ] = None,
        bead_bottle: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisBeadBottle)
        ] = None,
        powder_rack_positions: list[int] | None = None,
        crucible_return_positions: list[int] | None = None,
        crucible_return_location: str = "synthesis_crucible_rack_01",
    ) -> DoseRecipeResult:
        del powder_1, powder_2, powder_3, powder_4, bead_bottle
        with self._lock:
            return self._dose_recipe_impl(
                task_id=task_id,
                real_weights_json=real_weights_json,
                crucible=crucible,
                powder_rack_positions=powder_rack_positions,
                crucible_return_positions=crucible_return_positions,
                crucible_return_location=crucible_return_location,
            )

    def _start_resonance_impl(self, resolved: str) -> None:
        self._require_ok(self.station.start_acoustic_resonance(), "声共振启动")
        # 同步 TASK/LOT 业务状态；无 task_id 的上一条调用才是真正的
        # Modbus 工艺命令，两者分别对应 PLC 和上位机业务边界。
        self._require_ok(self.station.start_acoustic_resonance(resolved), "记录声共振启动")
        self._wait(
            lambda: int(self.station.controller.status().get("acoustic_resonance", -1)) == 2,
            "声共振完成",
        )
        self._resonance_started = True

    def _unload_resonance_impl(self, resolved: str) -> None:
        if not self._resonance_started:
            raise RuntimeError("声共振尚未完成，不能执行下料")
        self._require_ok(self.station.fetch_acoustic_resonance(), "声共振下料")
        self._wait(
            lambda: int(self.station.controller.status().get("acoustic_fetch", -1)) == 2,
            "声共振下料完成",
        )
        self._require_ok(self.station.fetch_acoustic_resonance(resolved), "记录声共振下料")
        self._require_ok(self.station.finish_acoustic_resonance(resolved), "结束声共振")
        current_location = str(
            self._ledger.items[self._big_crucible_id].get("location") or ""
        )
        if current_location not in {
            "synthesis_chamber",
            "synthesis_crucible_rack_01",
        }:
            raise RuntimeError(
                f"声共振取料位置不支持: {current_location!r}；"
                "应为合成位或命令3指定的回料架"
            )
        self._ledger.move(
            self._big_crucible_id,
            "bottle_station",
            expected=current_location,
        )
        self._ledger.update(self._big_crucible_id, synthesis_state="resonated")
        self._resonance_started = False

    @action(description="声共振上料并运行至完成")
    def start_resonance(
        self,
        task_id: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._start_resonance_impl(resolved)
            values: dict[str, Any] = {}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("RESONANCE_READY", "声共振运行完成，等待下料", **values)

    @action(description="声共振下料并完成物料位置记账")
    def unload_resonance(
        self,
        task_id: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._unload_resonance_impl(resolved)
            values: dict[str, Any] = {}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("RESONATED", "声共振完成，已到装瓶位", **values)

    @action(description="声共振上料、运行、下料并结束握手（兼容动作）")
    def resonate_and_unload(
        self,
        task_id: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._start_resonance_impl(resolved)
            self._unload_resonance_impl(resolved)
            values: dict[str, Any] = {}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("RESONATED", "声共振完成，已到装瓶位", **values)

    def _scan_bottle_impl(self, resolved: str, qrcode: str) -> str:
        code = str(qrcode or "").strip()
        if not code:
            lots = self._task_lots(resolved)
            code = str(lots[0].get("cubic") or "") if lots else ""
        if not code:
            raise RuntimeError("没有可用的大坩埚二维码")
        self._require_ok(self.station.scan_big_cubic_to_bottle(resolved, code), "扫码装瓶")
        self._bottle_scanned = True
        self._last_qrcode = code
        return code

    def _prepare_post_impl(self, resolved: str, qrcode: str) -> tuple[str, str]:
        if not self._bottle_scanned:
            raise RuntimeError("尚未完成大坩埚扫码装瓶")
        qrcode = str(qrcode or self._last_qrcode).strip()
        post = self._require_ok(self.station.create_post(resolved), "创建 POST")
        post_id = str(post.get("post_id") or (post.get("data") or {}).get("post_id") or "")
        if not post_id:
            raise RuntimeError(f"创建 POST 未返回 post_id: {post}")
        self._post_id = post_id
        self._require_ok(self.station.start_post(post_id), "启动 POST")
        lots = self._post_payload(post_id).get("LOT") or []
        lot_id = str(lots[0].get("lotId") or "") if lots else ""
        if not lot_id:
            raise RuntimeError("POST 中没有 LOT")
        self._require_ok(self.station.scan_lot_to_batch(post_id, lot_id), "扫描 LOT 到批次")
        self._require_ok(self.station.confirm_lot_batch(post_id, lot_id), "确认 LOT 批次")
        self._ledger.move(
            self._big_crucible_id,
            "post_processing_queue",
            expected="bottle_station",
            cubic_code=qrcode,
        )
        self._ledger.update(self._big_crucible_id, synthesis_state="bottled", lot_id=lot_id)
        self._bottle_scanned = False
        return post_id, lot_id

    @action(description="扫描大坩埚二维码并完成装瓶交接")
    def scan_bottle(
        self,
        task_id: str = "",
        qrcode: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            code = self._scan_bottle_impl(resolved, qrcode)
            values: dict[str, Any] = {"qrcode": code}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("BOTTLE_SCANNED", "大坩埚扫码装瓶完成，等待建立 POST", **values)

    @action(description="建立 POST 并扫描确认 LOT")
    def prepare_post(
        self,
        task_id: str = "",
        qrcode: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            code = str(qrcode or "").strip()
            if not self._bottle_scanned:
                code = self._scan_bottle_impl(resolved, code)
            else:
                code = code or self._last_qrcode
            post_id, lot_id = self._prepare_post_impl(resolved, code)
            values: dict[str, Any] = {"qrcode": code, "lot_id": lot_id}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("BOTTLED", "POST 和 LOT 已建立并确认", **values)

    @action(description="扫码装瓶并建立 POST（兼容动作）")
    def bottle_and_prepare_post(
        self,
        task_id: str = "",
        qrcode: str = "",
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            code = self._scan_bottle_impl(resolved, qrcode)
            post_id, lot_id = self._prepare_post_impl(resolved, code)
            values: dict[str, Any] = {"qrcode": code, "lot_id": lot_id}
            if crucible is not None:
                values["crucible"] = crucible
            return self._result("BOTTLED", "扫码装瓶并建立 POST 完成", **values)

    def _resolve_small_crucible(
        self, resolved_post: str, lot_id: str, firing_type: int
    ) -> tuple[str, str]:
        resolved_lot = str(lot_id or "").strip()
        payload = self._post_payload(resolved_post)
        if not resolved_lot:
            lots = payload.get("LOT") or []
            resolved_lot = str(lots[0].get("lotId") or "") if lots else ""
        lots = [
            lot
            for lot in payload.get("LOT") or []
            if str(lot.get("lotId") or "") == resolved_lot
        ]
        small = (lots[0].get("small_cubics") or []) if lots else []
        if not small:
            raise RuntimeError("LOT 没有可分配的小坩埚")
        code = str(small[0].get("small_cubic_id") or "")
        self._require_ok(
            self.station.scan_lot_to_small_cubic(
                resolved_post, resolved_lot, code, int(firing_type)
            ),
            "扫描 LOT 到小坩埚",
        )
        self._require_ok(
            self.station.complete_lot_to_small_cubic(resolved_post, resolved_lot, code),
            "完成小坩埚分配",
        )
        if code not in self._ledger.items:
            self._ledger.put(
                code,
                kind="small_crucible",
                location="small_crucible_rack",
                post_id=resolved_post,
                lot_id=resolved_lot,
                firing_type=int(firing_type),
            )
        self._small_crucible_assigned = True
        self._assigned_small_code = code
        return resolved_lot, code

    def _confirm_firing_schedule_impl(self, resolved_post: str) -> None:
        if not self._small_crucible_assigned:
            raise RuntimeError("尚未分配小坩埚，不能确认烧结计划")
        self._require_ok(
            self.station.confirm_joule_heating_schedule(resolved_post),
            "确认烧结计划",
        )

    @action(description="扫描 LOT 并分配小坩埚")
    def assign_small_crucible(
        self,
        post_id: str = "",
        lot_id: str = "",
        firing_type: int = 0,
        small_crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved_post = self._require_post(post_id)
            resolved_lot, code = self._resolve_small_crucible(
                resolved_post, lot_id, int(firing_type)
            )
            values: dict[str, Any] = {"lot_id": resolved_lot, "small_cubics": [code]}
            if small_crucible is not None:
                values["small_crucible"] = small_crucible
            return self._result("ASSIGNED", "小坩埚已分配", **values)

    @action(description="确认小坩埚烧结计划")
    def confirm_firing_schedule(
        self,
        post_id: str = "",
        lot_id: str = "",
        firing_type: int = 0,
        small_crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved_post = self._require_post(post_id)
            self._confirm_firing_schedule_impl(resolved_post)
            values: dict[str, Any] = {
                "lot_id": str(lot_id or ""),
                "small_cubics": [self._assigned_small_code],
            }
            if small_crucible is not None:
                values["small_crucible"] = small_crucible
            return self._result("SCHEDULED", "小坩埚烧结计划已确认", **values)

    @action(description="分配小坩埚并确认烧结计划（兼容动作）")
    def assign_small_crucibles(
        self,
        post_id: str = "",
        lot_id: str = "",
        firing_type: int = 0,
        small_crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved_post = self._require_post(post_id)
            resolved_lot, code = self._resolve_small_crucible(
                resolved_post, lot_id, int(firing_type)
            )
            self._confirm_firing_schedule_impl(resolved_post)
            values: dict[str, Any] = {"lot_id": resolved_lot, "small_cubics": [code]}
            if small_crucible is not None:
                values["small_crucible"] = small_crucible
            return self._result("SCHEDULED", "小坩埚分配和烧结计划已确认", **values)

    def _start_sinter_impl(self, resolved: str) -> None:
        if not self._small_crucible_assigned:
            raise RuntimeError("尚未分配小坩埚，不能启动烧结")
        self._require_ok(self.station.start_sintering(resolved), "启动烧结")
        if self.station.simulation:
            self.station.advance_simulation(
                float(getattr(self.station.business_state, "firing_duration", 2.0))
            )
        self._wait(lambda: bool((self._post_payload(resolved).get("LOT") or [])), "烧结状态")
        self._sinter_started = True

    def _unload_sinter_impl(self, resolved: str, firing_type: int) -> list[str]:
        if not self._sinter_started:
            raise RuntimeError("烧结尚未启动或完成，不能执行出炉")
        if int(firing_type) == 4:
            self._require_ok(self.station.fetch_joule_heating(resolved), "焦耳热下料")
        else:
            self._require_ok(
                self.station.fetch_furnace(resolved, int(firing_type) + 1),
                "马弗炉下料",
            )
        codes = [
            item_id
            for item_id, record in self._ledger.items.items()
            if record.get("kind") == "small_crucible" and record.get("post_id") == resolved
        ]
        for code in codes:
            self._ledger.move(code, "stock_buffer", expected="small_crucible_rack")
        # The physical batch is now complete.  Keep the ledger and POST
        # identifiers available for the optional store_fired_material action.
        self._batch_terminal = True
        self._sinter_started = False
        return codes

    @action(description="启动烧结并等待烧结完成")
    def start_sintering(
        self,
        post_id: str = "",
        small_crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_post(post_id)
            self._start_sinter_impl(resolved)
            values: dict[str, Any] = {"small_cubics": [self._assigned_small_code]}
            if small_crucible is not None:
                values["small_crucible"] = small_crucible
            return self._result("SINTERING", "烧结完成，等待出炉", **values)

    @action(description="烧结下料并把小坩埚记入待入库位")
    def unload_sintered(
        self,
        post_id: str = "",
        firing_type: int = 0,
        small_crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_post(post_id)
            codes = self._unload_sinter_impl(resolved, int(firing_type))
            values: dict[str, Any] = {"small_cubics": codes}
            if small_crucible is not None:
                values["small_crucible"] = small_crucible
            return self._result("UNLOADED", "烧结完成并已出炉", **values)

    @action(description="启动烧结、等待完成并取出小坩埚（兼容动作）")
    def sinter_and_unload(
        self,
        post_id: str = "",
        firing_type: int = 0,
        small_crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_post(post_id)
            self._start_sinter_impl(resolved)
            codes = self._unload_sinter_impl(resolved, int(firing_type))
            values: dict[str, Any] = {"small_cubics": codes}
            if small_crucible is not None:
                values["small_crucible"] = small_crucible
            return self._result("UNLOADED", "烧结完成并已出炉", **values)

    @action(description="扫描瓶码并把烧结物料记入成品库")
    def store_fired_material(self, small_cubic_id: str, bottle_code: str, lot_id: str = "") -> AtomicResult:
        with self._lock:
            code = str(small_cubic_id).strip()
            record = self._ledger.items.get(code)
            if record is None:
                raise ValueError(f"小坩埚尚未建账: {code}")
            post_id = self._require_post(str(record.get("post_id") or ""))
            resolved_lot = str(lot_id or record.get("lot_id") or "")
            self._require_ok(self.station.scan_bottle_to_stock(post_id, resolved_lot, str(bottle_code).strip()), "扫描瓶码入库")
            self._ledger.move(code, "stock", expected="stock_buffer", bottle_code=str(bottle_code).strip())
            return self._result("STORED", "烧结物料已入库", qrcode=str(bottle_code).strip(), lot_id=resolved_lot)

    @action(always_free=True, description="查询原子动作物料账")
    def resource_status(self) -> AtomicResult:
        return self._result(self.status, "物料账快照")

    @action(description="使用指定坩埚完成一次 PLC 扫码绑定和称粉")
    def sample_with_materials(
        self,
        crucible: Annotated[ResourceSlot, AllowedResourceTemplates(SynthesisCrucible)],
        source_site: str = "synthesis_crucible_rack_01-0",
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
    ) -> AtomicSamplingResult:
        """Expose the PLC composite sampling boundary on the atomic device."""

        return self.station.sample_with_materials(
            crucible=crucible,
            source_site=source_site,
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
        )


__all__ = ["YBSynthesisAtomicStation"]
