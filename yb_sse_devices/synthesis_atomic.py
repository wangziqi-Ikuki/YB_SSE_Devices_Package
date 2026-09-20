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

from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.registry.placeholder_type import ResourceSlot

from yb_sse_devices.resources.synthesis_resources import (
    SynthesisBeadBottle,
    SynthesisCrucible,
    SynthesisPowder,
)
from yb_sse_devices.synthesis_modbus_station import YBSynthesisModbusStation
from yb_sse_devices.synthesis_protocol import materials_from_columns, resolve_task_slots_input


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
        **_: Any,
    ) -> None:
        resolved = dict(config or {})
        resolved.setdefault("simulation", True)
        self.device_id = device_id or "yb_synthesis_atomic_station"
        self.action_timeout = max(0.1, float(resolved.get("action_timeout", action_timeout)))
        self.simulation_step = max(0.01, float(resolved.get("simulation_step", simulation_step)))
        self.station = YBSynthesisModbusStation(
            device_id=f"{self.device_id}_plc", config=resolved
        )
        self._lock = threading.RLock()
        self._ledger = _Ledger()
        self._task_id = ""
        self._post_id = ""
        self._task_slots: list[dict[str, Any]] = []
        self._materials: list[dict[str, Any]] = []
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
            if self.station.simulation:
                # Raw PLC handshakes must not advance the separate TASK/POST
                # business clock; doing so can auto-create a POST before the
                # explicit scan-and-bottle boundary.
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

    @action(description="创建并启动合成 TASK，同时建立物料初始账")
    def create_batch(
        self,
        recipe_name: str = "YB-SIM-Li6PS5Cl",
        formula: str = "Li6PS5Cl",
        synthesis_mass: float = 3.86,
        n_ball_bead: int = 80,
        powder_names: list[str] | None = None,
        powder_weights: list[float] | None = None,
        powder_tolerances: list[float] | None = None,
        powder_pre_adds: list[bool] | None = None,
        pallet_type: int = 1,
        cubic_type: int = 1,
        task_slot_nums: list[int] | None = None,
        has_bead_bottle: bool = True,
        bead_count: int = 80,
        fetch_cubic_source: int = 1,
    ) -> AtomicResult:
        with self._lock:
            if self._task_id and self._batch_terminal:
                # A completed batch can remain queryable for
                # ``store_fired_material``.  Starting another batch must not
                # reuse that task, post, or ledger state.
                self._task_id = ""
                self._post_id = ""
                self._task_slots = []
                self._materials = []
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
            materials = materials_from_columns(
                powder_names, powder_weights, powder_tolerances, powder_pre_adds
            )
            slots = resolve_task_slots_input(
                task_slot_nums,
                [str(recipe_name)] * max(1, len(task_slot_nums or [1])),
            )
            slots = [{"slot_num": int(item["slot_num"]), "recipe_name": str(recipe_name)} for item in slots]
            if fetch_cubic_source not in {0, 1}:
                raise ValueError("fetch_cubic_source 必须是 0（方舱）或 1（料架2）")
            recipe_response = self.station.upload_recipe(
                recipe_name, formula, synthesis_mass, n_ball_bead,
                powder_names=[str(item["name"]) for item in materials],
                powder_weights=[float(item["weight"]) for item in materials],
                powder_tolerances=[float(item["tolerance"]) for item in materials],
                powder_pre_adds=[bool(item["pre_add"]) for item in materials],
            )
            if not self._ok(recipe_response):
                # Recipe names are persistent PLC records.  Re-running a
                # completed batch with the same recipe is valid, and the PLC
                # reports the existing-name condition as result=4.  Reuse the
                # existing recipe; all other upload failures remain fatal.
                try:
                    duplicate_recipe = int(recipe_response.get("result", -1)) == 4
                except (AttributeError, TypeError, ValueError):
                    duplicate_recipe = False
                if not duplicate_recipe:
                    self._require_ok(recipe_response, "上传配方")
            created = self._require_ok(
                self.station.create_task(
                    pallet_type=pallet_type,
                    cubic_type=cubic_type,
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
            self._task_slots = slots
            self._materials = materials
            self._bead_count = int(bead_count) if has_bead_bottle else 0
            self._big_crucible_id = str(getattr(self.station, "simulation_crucible_id", "CRU-SIM-001"))
            source = "cabin" if fetch_cubic_source == 0 else "crucible_rack_2"
            self._ledger.put(self._big_crucible_id, kind="big_crucible", location=source, task_id=task_id, cubic_type=cubic_type)
            for index, material in enumerate(materials, start=1):
                self._ledger.put(
                    f"powder:{task_id}:{index}:{material['name']}",
                    kind="powder", location="powder_store", material=str(material["name"]),
                    quantity=float(material["weight"]), pre_add=bool(material["pre_add"]),
                )
            if has_bead_bottle:
                self._ledger.put(f"beads:{task_id}", kind="ball_beads", location="bead_bottle", quantity=int(bead_count))
            return self._result("READY", "TASK 已创建并启动")

    @action(description="取大坩埚并完成方舱关门握手")
    def load_big_crucible(
        self,
        task_id: str = "",
        fetch_cubic_source: int | None = None,
        crucible: Annotated[
            ResourceSlot | None, AllowedResourceTemplates(SynthesisCrucible)
        ] = None,
    ) -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            source = fetch_cubic_source
            if source is None:
                source = 0 if self._ledger.items[self._big_crucible_id]["location"] == "cabin" else 1
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

    def _parse_real_weights(self, real_weights_json: str) -> dict[str, float]:
        if not real_weights_json.strip():
            return {}
        raw = json.loads(real_weights_json)
        if not isinstance(raw, dict):
            raise ValueError("real_weights_json 必须是 {物料名: 实际重量} 对象")
        return {str(key): float(value) for key, value in raw.items()}

    def _confirm_recipe_impl(self, resolved: str, weights: dict[str, float]) -> None:
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
    ) -> AtomicResult:
        resolved = self._require_task(task_id)
        weights = self._parse_real_weights(real_weights_json)
        if not self._recipe_confirmed:
            self._confirm_recipe_impl(resolved, weights)
        elif weights:
            # A direct caller may still provide the actual weights at this
            # boundary; use them without sending a duplicate confirmation.
            self._confirmed_weights.update(weights)
        self._require_ok(self.station.start_recipt(resolved), "启动加样")
        sampling_results: list[dict[str, Any]] = []
        for slot in self._task_slots:
            names = [str(item["name"]) for item in self._materials]
            sample = self.station.sample(
                task_id=resolved,
                slot_num=int(slot["slot_num"]),
                rack_positions=list(range(1, len(names) + 1)),
                masses=[float(item["weight"]) for item in self._materials],
                tolerances=[float(item["tolerance"]) for item in self._materials],
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
            contents="recipe_powder_and_ball_beads",
            synthesis_state="dosed",
        )
        values: dict[str, Any] = {"sampling_results": sampling_results}
        if crucible is not None:
            values["crucible"] = crucible
        return self._result("DOSED", "内部扫码、称粉和加珠已完成", **values)

    @action(description="内部扫码、称粉和加珠（PLC CMD_SAMPLE 连续动作）")
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
    ) -> AtomicResult:
        del powder_1, powder_2, powder_3, powder_4, bead_bottle
        with self._lock:
            return self._dose_recipe_impl(
                task_id=task_id,
                real_weights_json=real_weights_json,
                crucible=crucible,
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
        self._ledger.move(self._big_crucible_id, "bottle_station", expected="synthesis_chamber")
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


__all__ = ["YBSynthesisAtomicStation"]
