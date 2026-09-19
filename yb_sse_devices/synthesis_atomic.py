"""YB 合成工站的 OS 原子动作层。

``YBSynthesisModbusStation`` 提供的是 PLC/上位机协议动作；本模块把这些
动作按不可中断的物理过程重新编排，并在 PLC/仿真反馈完成后提交一份
设备包侧物料位置账。默认使用设备包内的 Modbus 和业务仿真，不连接真实 PLC。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, TypedDict

from unilabos.registry.decorators import action, device, not_action, topic_config

from yb_sse_devices.synthesis_modbus_station import YBSynthesisModbusStation
from yb_sse_devices.synthesis_protocol import materials_from_columns, resolve_task_slots_input


class AtomicResult(TypedDict, total=False):
    task_id: str
    post_id: str
    state: str
    message: str
    qrcode: str
    lot_id: str
    small_cubics: list[str]
    ledger_json: str


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
        self._big_crucible_id = ""
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
            self._require_ok(
                self.station.upload_recipe(
                    recipe_name, formula, synthesis_mass, n_ball_bead,
                    powder_names=[str(item["name"]) for item in materials],
                    powder_weights=[float(item["weight"]) for item in materials],
                    powder_tolerances=[float(item["tolerance"]) for item in materials],
                    powder_pre_adds=[bool(item["pre_add"]) for item in materials],
                ),
                "上传配方",
            )
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
    def load_big_crucible(self, task_id: str = "", fetch_cubic_source: int | None = None) -> AtomicResult:
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
            return self._result("LOADED", "大坩埚已到合成位")

    @action(description="完成内部扫码绑定、称粉和加珠")
    def dose_recipe(self, task_id: str = "", real_weights_json: str = "") -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            weights: dict[str, float] = {}
            if real_weights_json.strip():
                raw = json.loads(real_weights_json)
                if not isinstance(raw, dict):
                    raise ValueError("real_weights_json 必须是 {物料名: 实际重量} 对象")
                weights = {str(key): float(value) for key, value in raw.items()}
            for slot in self._task_slots:
                for material in self._materials:
                    if material.get("pre_add"):
                        self._require_ok(
                            self.station.confirm_recipe(
                                resolved, int(slot["slot_num"]), str(material["name"]),
                                float(weights.get(str(material["name"]), material["weight"])),
                            ),
                            "确认预加料",
                        )
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
                    bead_count=0,
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
                    self._ledger.move(item_id, self._big_crucible_id, expected="powder_store", actual_weight=weights.get(str(record["material"]), record["quantity"]))
            bead_id = f"beads:{resolved}"
            if bead_id in self._ledger.items:
                self._ledger.move(bead_id, self._big_crucible_id, expected="bead_bottle")
            self._ledger.update(self._big_crucible_id, contents="recipe_powder_and_ball_beads", synthesis_state="dosed")
            return self._result("DOSED", "内部扫码、称粉和加珠已完成", sampling_results=sampling_results)

    @action(description="声共振上料、运行、下料并结束握手")
    def resonate_and_unload(self, task_id: str = "") -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            self._require_ok(self.station.start_acoustic_resonance(), "声共振启动")
            # 同步 TASK/LOT 业务状态；无 task_id 的上一条调用才是真正的
            # Modbus 工艺命令，两者分别对应 PLC 和上位机业务边界。
            self._require_ok(self.station.start_acoustic_resonance(resolved), "记录声共振启动")
            self._wait(lambda: int(self.station.controller.status().get("acoustic_resonance", -1)) == 2, "声共振完成")
            self._require_ok(self.station.fetch_acoustic_resonance(), "声共振下料")
            self._wait(lambda: int(self.station.controller.status().get("acoustic_fetch", -1)) == 2, "声共振下料完成")
            self._require_ok(self.station.fetch_acoustic_resonance(resolved), "记录声共振下料")
            self._require_ok(self.station.finish_acoustic_resonance(resolved), "结束声共振")
            self._ledger.move(self._big_crucible_id, "bottle_station", expected="synthesis_chamber")
            self._ledger.update(self._big_crucible_id, synthesis_state="resonated")
            return self._result("RESONATED", "声共振完成，已到装瓶位")

    @action(description="扫码装瓶并建立 POST")
    def bottle_and_prepare_post(self, task_id: str = "", qrcode: str = "") -> AtomicResult:
        with self._lock:
            resolved = self._require_task(task_id)
            if not qrcode:
                lots = self._task_lots(resolved)
                qrcode = str(lots[0].get("cubic") or "") if lots else ""
            if not qrcode:
                raise RuntimeError("没有可用的大坩埚二维码")
            self._require_ok(self.station.scan_big_cubic_to_bottle(resolved, qrcode), "扫码装瓶")
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
            self._ledger.move(self._big_crucible_id, "post_processing_queue", expected="bottle_station", cubic_code=qrcode)
            self._ledger.update(self._big_crucible_id, synthesis_state="bottled", lot_id=lot_id)
            return self._result("BOTTLED", "扫码装瓶并建立 POST 完成", qrcode=qrcode, lot_id=lot_id)

    @action(description="分配小坩埚并确认烧结计划")
    def assign_small_crucibles(self, post_id: str = "", lot_id: str = "", firing_type: int = 0) -> AtomicResult:
        with self._lock:
            resolved_post = self._require_post(post_id)
            resolved_lot = str(lot_id or "").strip()
            if not resolved_lot:
                payload = self._post_payload(resolved_post)
                lots = payload.get("LOT") or []
                resolved_lot = str(lots[0].get("lotId") or "") if lots else ""
            payload = self._post_payload(resolved_post)
            lots = [lot for lot in payload.get("LOT") or [] if str(lot.get("lotId") or "") == resolved_lot]
            small = (lots[0].get("small_cubics") or []) if lots else []
            if not small:
                raise RuntimeError("LOT 没有可分配的小坩埚")
            code = str(small[0].get("small_cubic_id") or "")
            self._require_ok(self.station.scan_lot_to_small_cubic(resolved_post, resolved_lot, code, int(firing_type)), "扫描 LOT 到小坩埚")
            self._require_ok(self.station.complete_lot_to_small_cubic(resolved_post, resolved_lot, code), "完成小坩埚分配")
            self._require_ok(self.station.confirm_joule_heating_schedule(resolved_post), "确认烧结计划")
            self._ledger.put(code, kind="small_crucible", location="small_crucible_rack", post_id=resolved_post, lot_id=resolved_lot, firing_type=int(firing_type))
            return self._result("SCHEDULED", "小坩埚分配和烧结计划已确认", lot_id=resolved_lot, small_cubics=[code])

    @action(description="启动烧结、等待完成并取出小坩埚")
    def sinter_and_unload(self, post_id: str = "", firing_type: int = 0) -> AtomicResult:
        with self._lock:
            resolved = self._require_post(post_id)
            self._require_ok(self.station.start_sintering(resolved), "启动烧结")
            if self.station.simulation:
                self.station.advance_simulation(float(getattr(self.station.business_state, "firing_duration", 2.0)))
            self._wait(lambda: bool((self._post_payload(resolved).get("LOT") or [])), "烧结状态")
            if int(firing_type) == 4:
                self._require_ok(self.station.fetch_joule_heating(resolved), "焦耳热下料")
            else:
                self._require_ok(self.station.fetch_furnace(resolved, int(firing_type) + 1), "马弗炉下料")
            codes = [item_id for item_id, record in self._ledger.items.items() if record.get("kind") == "small_crucible" and record.get("post_id") == resolved]
            for code in codes:
                self._ledger.move(code, "stock_buffer", expected="small_crucible_rack")
            return self._result("UNLOADED", "烧结完成并已出炉", small_cubics=codes)

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
