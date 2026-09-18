"""设备包业务仿真的薄适配层。

合成工站的配方、TASK、POST、LOT 协议已经由
``synthesis_mock_server.MockSynthesisState`` 实现。直连设备入口需要一个
稳定、可注入的对象，因此这里仅适配它的 ``handle`` 接口，并给仿真时钟、
暂停/恢复、断线/重连和重置提供设备包动作所需的最小控制面。这里不复制
业务状态机，也不引入 TCP 或 Uni-Lab-OS 依赖。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import time
from typing import Any, Mapping, MutableMapping


class BusinessSimulation:
    """对既有 ``MockSynthesisState`` 的无网络适配器。

    ``advance`` 使用模拟时间推进既有 Mock 的任务和 POST 步骤；动作本身
    仍由 Mock 按文档错误码校验。使用 ``store`` 时保存的是可移植快照，
    可在同一进程中创建新适配器后恢复状态。
    """

    def __init__(
        self,
        *,
        acoustic_duration: float = 1.0,
        sampling_duration: float = 0.5,
        firing_duration: float = 2.0,
        store: MutableMapping[str, Any] | None = None,
        auto_advance: bool = False,
        step_interval: float | None = None,
        load_demo: bool = False,
    ) -> None:
        from yb_sse_devices.synthesis_mock_server import MockSynthesisState

        self.acoustic_duration = float(acoustic_duration)
        self.sampling_duration = float(sampling_duration)
        self.firing_duration = float(firing_duration)
        self.store = store
        # Wall-clock auto advance is disabled by default.  ``advance`` below
        # calls the same private progression function explicitly, so tests are
        # deterministic and do not need sleeps.
        interval = float(step_interval if step_interval is not None else sampling_duration or 0.1)
        self.state = MockSynthesisState(
            step_interval=max(0.01, interval),
            auto_advance=bool(auto_advance),
            load_demo=bool(load_demo),
        )
        self.clock = 0.0
        self.connected = True
        self.paused = False
        self._task_elapsed: dict[str, float] = {}
        self._post_elapsed: dict[str, float] = {}
        if store is not None and store.get("business_snapshot"):
            self.restore(store["business_snapshot"])

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """处理与旧 TCP Mock 相同的动作请求。"""
        if not self.connected:
            return {"request_id": request.get("request_id"), "result": 1}
        action = str(request.get("action") or "")
        if self.paused and action in {
            "start_recipt", "start_acoustic_resonance", "start_sintering",
        }:
            return {"request_id": request.get("request_id"), "result": 9}
        if action in {
            "create_post", "start_post", "scan_lot_to_batch", "confirm_lot_batch",
            "scan_lot_to_small_cubic", "complete_lot_to_small_cubic",
            "confirm_joule_heating_schedule", "scan_bottle_to_stock",
        }:
            return self._handle_post_action(
                request.get("request_id"), action, request.get("param") or {}
            )
        response = self.state.handle(dict(request))
        param = request.get("param") or {}
        if action == "start_task":
            task_id = str(param.get("task_id") or "").strip()
            if task_id and int(response.get("result", 1)) == 0:
                self._task_elapsed.setdefault(task_id, 0.0)
        # Explicit supervisory actions can complete a TASK before the legacy
        # wall-clock loop reaches its next step.  Remove its wall-clock anchor
        # at that boundary so a later query cannot regress task_state=4 back
        # to the step-0 value.
        if action in {"finish_acoustic_resonance", "scan_big_cubic_to_bottle"}:
            task_id = str(param.get("task_id") or "").strip()
            if task_id:
                task = self.state._find_task(task_id)
                if task is not None and int(task.get("task_state") or 0) >= 4:
                    self.state.task_started.pop(task_id, None)
        return response

    def execute(self, action: str, **payload: Any) -> dict[str, Any]:
        """调用适配器而无需构造 TCP 请求包，便于设备动作和单元测试复用。"""

        return self.handle({"request_id": None, "action": action, "param": payload})

    @staticmethod
    def _response(request_id: Any, result: int = 0, data: dict[str, Any] | None = None) -> dict[str, Any]:
        response: dict[str, Any] = {"request_id": request_id, "result": int(result)}
        if data is not None:
            response["data"] = data
        return response

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _post_lot(self, post: dict[str, Any], lot_id: str) -> dict[str, Any] | None:
        wanted = str(lot_id or "").strip()
        for lot in post.get("LOT") or []:
            if wanted and str(lot.get("lotId") or "") == wanted:
                return lot
        return None

    def _find_post_for_lot(self, lot_id: str) -> dict[str, Any] | None:
        wanted = str(lot_id or "").strip()
        for post in self.state.posts:
            if self._post_lot(post, wanted) is not None:
                return post
        return None

    def _ensure_post_for_task(self, task: dict[str, Any]) -> dict[str, Any] | None:
        """Create one explicit POST for a finished TASK, idempotently."""

        task_id = str(task.get("task_id") or "")
        for post in self.state.posts:
            if str(post.get("_task_id") or "") == task_id:
                return post
        before = {str(item.get("post_id") or "") for item in self.state.posts}
        self.state._ensure_post_for_task(task)
        for post in self.state.posts:
            if str(post.get("post_id") or "") not in before:
                post["_task_id"] = task_id
                return post
        return None

    def _handle_post_action(
        self, request_id: Any, action: str, raw_param: Any
    ) -> dict[str, Any]:
        """补齐使用说明中的 POST/LOT 扫描边界。

        老 TCP Mock 会在自动推进时隐式创建 POST，但直连模式必须把扫码、
        批次确认和装瓶动作暴露为设备动作，否则工作流无法表达人工扫码的
        失败、重复和顺序约束。这里复用旧 Mock 的 LOT/烧结数据结构，新增的
        字段均为内部仿真元数据，不伪造 Modbus 寄存器。
        """

        param = dict(raw_param) if isinstance(raw_param, Mapping) else {}
        with self.state.lock:
            self.state._update()
            if action == "create_post":
                task_id = str(param.get("task_id") or "").strip()
                task = self.state._find_task(task_id)
                if task is None:
                    return self._response(request_id, 7)
                if int(task.get("task_state") or 0) < 4:
                    return self._response(request_id, 9)
                post = self._ensure_post_for_task(task)
                if post is None:
                    return self._response(request_id, 3)
                return self._response(
                    request_id,
                    data={"post_id": str(post.get("post_id") or ""), "task_id": task_id},
                )

            post_id = str(param.get("post_id") or "").strip()
            post = self.state._find_post(post_id) if post_id else None
            if post is None and action == "start_post" and not post_id:
                post = next((item for item in self.state.posts if int(item.get("post_state") or 0) < 7), None)
            if post is None:
                return self._response(request_id, 7)
            post_id = str(post.get("post_id") or "")

            if action == "start_post":
                if int(post.get("post_state") or 0) != 1:
                    return self._response(request_id, 9)
                post["post_state"] = 2
                # ``MockSynthesisState``'s optional wall-clock mode expects a
                # monotonic timestamp.  The adapter's own ``clock`` is a
                # deterministic elapsed counter and must not be mixed with
                # that timestamp domain.
                self.post_started.setdefault(post_id, time.monotonic())
                self._post_elapsed.setdefault(post_id, 0.0)
                return self._response(request_id, data={"post_id": post_id})

            if action == "scan_lot_to_batch":
                if int(post.get("post_state") or 0) < 2 or int(post.get("post_state") or 0) >= 7:
                    return self._response(request_id, 9)
                lot_id = str(param.get("lot_id") or "").strip()
                qrcode = str(param.get("qrcode") or "").strip()
                lot = self._post_lot(post, lot_id)
                if lot is None and qrcode:
                    lot = next(
                        (item for item in post.get("LOT") or []
                         if qrcode in {str(item.get("lotId") or ""), str(item.get("cubic") or "")}),
                        None,
                    )
                if lot is None:
                    return self._response(request_id, 3)
                scanned = post.setdefault("_batch_lots", [])
                if str(lot.get("lotId") or "") not in scanned:
                    scanned.append(str(lot.get("lotId") or ""))
                return self._response(request_id, data={"post_id": post_id, "lot_id": lot.get("lotId")})

            if action == "confirm_lot_batch":
                if int(post.get("post_state") or 0) < 2 or not post.get("_batch_lots"):
                    return self._response(request_id, 9)
                lot_id = str(param.get("lot_id") or "").strip()
                if lot_id and lot_id not in post.get("_batch_lots", []):
                    return self._response(request_id, 3)
                post["_batch_confirmed"] = True
                post["post_state"] = max(int(post.get("post_state") or 0), 3)
                return self._response(request_id, data={"post_id": post_id})

            if action == "scan_lot_to_small_cubic":
                if int(post.get("post_state") or 0) < 3 or int(post.get("post_state") or 0) >= 7:
                    return self._response(request_id, 9)
                lot_id = str(param.get("lot_id") or "").strip()
                small_id = str(param.get("small_cubic_id") or "").strip()
                lot = self._post_lot(post, lot_id)
                if lot is None or not small_id:
                    return self._response(request_id, 3)
                cubic = next(
                    (item for item in lot.get("small_cubics") or []
                     if str(item.get("small_cubic_id") or "") == small_id),
                    None,
                )
                if cubic is None:
                    return self._response(request_id, 3)
                try:
                    firing_type = int(param.get("firing_type") or 0)
                except (TypeError, ValueError):
                    return self._response(request_id, 2)
                if firing_type not in {0, 1, 2, 3, 4}:
                    return self._response(request_id, 2)
                cubic["firing_type"] = firing_type
                cubic["send_state"] = 0
                lot.setdefault("_assigned_small_cubics", []).append(small_id)
                return self._response(request_id, data={"post_id": post_id, "lot_id": lot_id, "small_cubic_id": small_id})

            if action == "complete_lot_to_small_cubic":
                if int(post.get("post_state") or 0) < 3 or not post.get("_batch_confirmed"):
                    return self._response(request_id, 9)
                lot_id = str(param.get("lot_id") or "").strip()
                small_id = str(param.get("small_cubic_id") or "").strip()
                lot = self._post_lot(post, lot_id)
                if lot is None or small_id not in lot.get("_assigned_small_cubics", []):
                    return self._response(request_id, 9)
                post["_small_cubics_confirmed"] = True
                post["post_state"] = max(int(post.get("post_state") or 0), 4)
                return self._response(request_id, data={"post_id": post_id, "lot_id": lot_id})

            if action == "confirm_joule_heating_schedule":
                if int(post.get("post_state") or 0) < 4:
                    return self._response(request_id, 9)
                mode = str(param.get("joule_heating_fetch_mode") or "auto").strip().lower()
                if mode not in {"auto", "manual"}:
                    return self._response(request_id, 2)
                post["_joule_heating_fetch_mode"] = mode
                post["_joule_schedule_confirmed"] = True
                return self._response(request_id, data={"post_id": post_id, "mode": mode})

            if action == "scan_bottle_to_stock":
                if int(post.get("post_state") or 0) < 5:
                    return self._response(request_id, 9)
                lot_id = str(param.get("lot_id") or "").strip()
                bottle_code = str(param.get("bottle_code") or "").strip()
                lot = self._post_lot(post, lot_id) if lot_id else None
                if lot is None:
                    lot = next((item for item in post.get("LOT") or [] if any(not c.get("bottle_code") for c in item.get("small_cubics") or [])), None)
                if lot is None or not bottle_code:
                    return self._response(request_id, 3)
                if self.state._find_bottle(bottle_code) is not None:
                    return self._response(request_id, 9)
                cubic = next((item for item in lot.get("small_cubics") or [] if not item.get("bottle_code")), None)
                if cubic is None:
                    return self._response(request_id, 13)
                stamp = self._stamp()
                cubic["bottle_code"] = bottle_code
                cubic["scan_to_bottle_time"] = stamp
                cubic["fetch_state"] = 2
                lot["bottle_state"] = 1
                lot["bottle_time"] = stamp
                lot["lot_state"] = 7
                all_bottled = all(item.get("bottle_code") for item in lot.get("small_cubics") or [])
                if all_bottled:
                    post["post_state"] = 7
                return self._response(request_id, data={"post_id": post_id, "lot_id": lot.get("lotId"), "bottle_code": bottle_code})

            return self._response(request_id, 2)

    def advance(self, seconds: float = 0.1) -> dict[str, Any]:
        value = float(seconds)
        if value < 0:
            raise ValueError("seconds 不能为负数")
        if self.paused:
            return self.snapshot()
        self.clock += value
        # MockSynthesisState already owns all state transitions; invoke its
        # progression methods with deterministic step counts instead of using
        # monotonic wall time.  Existing direct actions remain unchanged.
        for task in self.state.tasks:
            task_id = str(task.get("task_id") or "")
            task_state = int(task.get("task_state") or 0)
            if task_state > 0 and task_state < 5:
                elapsed = self._task_elapsed.get(task_id, 0.0) + value
                self._task_elapsed[task_id] = elapsed
                steps = int(elapsed / self.state.step_interval)
                if steps:
                    self._advance_task_monotonic(task, steps)
                    self._task_elapsed[task_id] = elapsed % self.state.step_interval
            # ``finish_acoustic_resonance`` leaves the task at state 4.  The
            # legacy Mock normally creates POST on its wall-clock step 5;
            # create the same boundary deterministically when advancing the
            # adapter clock.
            if int(task.get("task_state") or 0) >= 4:
                self.state._ensure_post_for_task(task)
        for post in self.state.posts:
            post_id = str(post.get("post_id") or "")
            if post_id in self._post_elapsed and int(post.get("post_state") or 0) < 7:
                elapsed = self._post_elapsed.get(post_id, 0.0) + value
                self._post_elapsed[post_id] = elapsed
                steps = int(elapsed / self.state.step_interval)
                if steps:
                    self._advance_post_monotonic(post, steps)
                    self._post_elapsed[post_id] = elapsed % self.state.step_interval
        self._save()
        return self.snapshot()

    def _advance_task_monotonic(self, task: dict[str, Any], steps: int) -> None:
        current = int(task.get("task_state") or 0)
        target = min(5, current + max(0, int(steps)))
        if target <= current:
            return
        # _advance_task uses absolute legacy steps; these values are the
        # first step that yields each externally visible task state.
        legacy_step = {1: 0, 2: 2, 3: 3, 4: 5, 5: 6}[target]
        self.state._advance_task(task, legacy_step)

    def _advance_post_monotonic(self, post: dict[str, Any], steps: int) -> None:
        current = int(post.get("post_state") or 0)
        target = min(7, current + max(0, int(steps)))
        if target <= current:
            return
        self.state._advance_post(post, target - 1)

    def pause(self) -> dict[str, Any]:
        self.paused = True
        return self.snapshot()

    def resume(self) -> dict[str, Any]:
        self.paused = False
        return self.snapshot()

    def disconnect(self) -> dict[str, Any]:
        self.connected = False
        return self.snapshot()

    def reconnect(self) -> dict[str, Any]:
        self.connected = True
        return self.snapshot()

    def reset(self) -> dict[str, Any]:
        self.state.reset()
        self.clock = 0.0
        self.connected = True
        self.paused = False
        self._task_elapsed.clear()
        self._post_elapsed.clear()
        self._save()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """返回 Mock 状态和适配器控制面，可用于断点恢复。"""
        return {
            "clock": self.clock,
            "connected": self.connected,
            "paused": self.paused,
            "task_elapsed": dict(self._task_elapsed),
            "post_elapsed": dict(self._post_elapsed),
            "state": self.export_state(),
        }

    def export_state(self) -> dict[str, Any]:
        """导出既有 Mock 的内存字段，不依赖其是否提供持久化 API。"""
        fields = (
            "devices", "recipes", "tasks", "posts", "task_started", "post_started",
            "_task_seq", "_post_seq", "_lot_seq", "_cubic_seq", "last_client",
            "last_action", "auto_advance", "step_interval",
        )
        return {name: deepcopy(getattr(self.state, name)) for name in fields if hasattr(self.state, name)}

    def import_state(self, payload: Mapping[str, Any]) -> None:
        for name, value in payload.items():
            if name in {"lock", "step_interval"}:
                continue
            if hasattr(self.state, name):
                setattr(self.state, name, deepcopy(value))

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.clock = float(payload.get("clock", 0.0))
        self.connected = bool(payload.get("connected", True))
        self.paused = bool(payload.get("paused", False))
        self._task_elapsed = {str(k): float(v) for k, v in (payload.get("task_elapsed") or {}).items()}
        self._post_elapsed = {str(k): float(v) for k, v in (payload.get("post_elapsed") or {}).items()}
        raw = payload.get("state")
        if isinstance(raw, Mapping):
            self.import_state(raw)

    def _save(self) -> None:
        if self.store is not None:
            self.store["business_snapshot"] = self.snapshot()

    def __getattr__(self, name: str) -> Any:
        """Expose read-only Mock helpers for diagnostics and tests."""
        return getattr(self.state, name)


BusinessSimulator = BusinessSimulation

__all__ = ["BusinessSimulation", "BusinessSimulator"]
