"""合成工站 TCP 协议模拟服务端，可选本机交互页改设备状态与任务。

请求仍按客户端 compact JSON + CRLF 读取；响应按真机格式：缩进 JSON + LF。
"""

from __future__ import annotations

import argparse
import copy
import json
import socketserver
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from yb_sse_devices.synthesis_protocol import (
    BINARY_DEVICE_KEYS,
    DEVICE_KEYS,
    DEVICE_LABELS,
    MULTISTATE_DEVICE_KEYS,
    RECIPE_META_KEYS,
    RESULT_MESSAGES,
    is_running_post_state,
    is_running_task_state,
    offline_devices,
    online_devices,
    pallet_slot_count,
    recipe_component_count,
    small_cubic_id,
)

DEMO_RECIPE_NAME = "260708-Li6PS5Cl-R1"
DEMO_TASK_ID = "TASK-000168"
DEMO_POST_ID = "POST-000198"
DEMO_LOT_ID = "LOT-260813-001"


def encode_station_response(response: dict[str, Any]) -> bytes:
    """按真机格式编码 TCP 响应：缩进 JSON、键排序，以 LF 结束。"""
    return (
        json.dumps(response, ensure_ascii=False, indent=4, sort_keys=True) + "\n"
    ).encode("utf-8")


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _demo_recipe() -> dict[str, Any]:
    return {
        "recipe_name": DEMO_RECIPE_NAME,
        "formula": "Li6PS5Cl",
        "synthesis_mass": 3.86,
        "n_ball_bead": 80,
        "id": "20260717162404318",
        "Li2S": {"pre-add": False, "weight": 0.9, "tolerance": 0.0007},
        "LiBr": {"pre-add": True, "weight": 0.87, "tolerance": 0.0005},
        "LiCl": {"pre-add": False, "weight": 1.2, "tolerance": 0.001},
        "P2S5": {"pre-add": False, "weight": 0.88, "tolerance": 0.0005},
    }


def _with_real_weights(recipe: dict[str, Any]) -> dict[str, Any]:
    filled = copy.deepcopy(recipe)
    for key, value in filled.items():
        if key in RECIPE_META_KEYS or not isinstance(value, dict):
            continue
        weight = float(value.get("weight") or 0)
        value["real_weight"] = round(weight - 0.0001, 16) if not value.get("pre-add") else weight
    return filled


def _demo_lot() -> dict[str, Any]:
    return {
        "lotId": DEMO_LOT_ID,
        "lot_state": 6,
        "recipe_name": DEMO_RECIPE_NAME,
        "recipe_component": 4,
        "recipe": _with_real_weights(_demo_recipe()),
        "cubic": "CRU-L-001",
        "cubic_type": 1,
        "start_sampling_time": "2026-08-13 14:14:59",
        "end_sampling_time": "2026-08-13 14:15:04",
        "bottle_state": 0,
        "bottle_time": "",
        "acoustic_resonance_time": "2026-08-13 14:23:07",
        "acoustic_resonance_count": 1,
        "acoustic_resonance_params": [
            {
                "segs": [
                    {
                        "acceleration": 40,
                        "frequency": 55,
                        "id": "d398d9d5-ebd8-429b-8e65-98c9afca4a34",
                        "run_time": 1100,
                    },
                    {
                        "acceleration": 0,
                        "frequency": 0,
                        "id": "768329b7-e5b7-4de3-bb8b-ca952c1f295c",
                        "run_time": 700,
                    },
                    {
                        "acceleration": 50,
                        "frequency": 65,
                        "id": "7ddfb9f5-a9b9-493b-8f17-271680c60019",
                        "run_time": 1300,
                    },
                ]
            }
        ],
        "small_cubics": [
            {
                "small_cubic_id": "CRU-S-001",
                "create_date": "260813",
                "firing_type": 0,
                "firing_params": {
                    "open_door_temp": 160,
                    "segs": [
                        {
                            "begin_temp": 30,
                            "end_temp": 465,
                            "id": "bdf0a20d-c33d-4d45-8c6c-e02dca3c5fab",
                            "temp_time": 145,
                        },
                        {
                            "begin_temp": 465,
                            "end_temp": 465,
                            "id": "0d3ccc22-5ad1-49d0-9fb9-591d5d495a43",
                            "temp_time": 600,
                        },
                        {
                            "begin_temp": 475,
                            "end_temp": 475,
                            "id": "5b6a51a7-f18d-4f50-bfb1-4da9d5c32774",
                            "temp_time": 700,
                        },
                    ],
                },
                "send_state": 2,
                "fetch_state": 0,
                "start_firing_time": "2026-08-13 14:35:23",
                "end_firing_time": "",
                "scan_to_bottle_time": "",
                "bottle_code": "LOT-260813-001-CRU-S-001-260813",
            },
            {
                "small_cubic_id": "CRU-S-005",
                "create_date": "260813",
                "firing_type": 4,
                "firing_params": {
                    "hold_time": 1,
                    "id": "26230bf6-d7a2-439a-af2f-6b7c386196e4",
                    "name": "P1",
                    "temp": 480,
                },
                "send_state": 2,
                "fetch_state": 2,
                "start_firing_time": "2026-08-13 14:37:02",
                "end_firing_time": "2026-08-13 14:37:07",
                "scan_to_bottle_time": "2026-08-13 14:43:43",
                "bottle_code": "LOT-260813-001-CRU-S-005-260813",
            },
        ],
    }


class MockSynthesisState:
    """线程安全的配方 / TASK / POST 模拟状态。"""

    def __init__(
        self,
        *,
        step_interval: float = 0.1,
        devices: dict[str, int] | None = None,
        auto_advance: bool = True,
        load_demo: bool = False,
    ) -> None:
        self.step_interval = max(0.01, float(step_interval))
        self.auto_advance = bool(auto_advance)
        self.lock = threading.RLock()
        self.devices = dict(devices or online_devices())
        self.recipes: dict[str, dict[str, Any]] = {}
        self.tasks: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        self.task_started: dict[str, float] = {}
        self.post_started: dict[str, float] = {}
        self._task_seq = 168
        self._post_seq = 198
        self._lot_seq = 1
        self._cubic_seq = 1
        self.last_client = ""
        self.last_action: dict[str, Any] | None = None
        if load_demo:
            self.load_demo()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._update()
            running_tasks = [
                item["task_id"]
                for item in self.tasks
                if is_running_task_state(int(item.get("task_state") or 0))
            ]
            running_posts = [
                item["post_id"]
                for item in self.posts
                if is_running_post_state(int(item.get("post_state") or 0))
            ]
            return {
                "devices": dict(self.devices),
                "recipe_names": list(self.recipes),
                "task_count": len(self.tasks),
                "post_count": len(self.posts),
                "running_tasks": running_tasks,
                "running_posts": running_posts,
                "current_task_id": running_tasks[0] if running_tasks else "",
                "current_post_id": running_posts[0] if running_posts else "",
                "last_client": self.last_client,
                "last_action": None if not self.last_action else dict(self.last_action),
                "auto_advance": self.auto_advance,
            }

    def set_device(self, key: str, value: int) -> None:
        if key not in DEVICE_KEYS:
            raise ValueError(f"未知设备: {key}")
        with self.lock:
            self.devices[key] = int(value)

    def set_auto_advance(self, enabled: bool) -> None:
        with self.lock:
            self.auto_advance = bool(enabled)

    def reset(self) -> None:
        with self.lock:
            self.devices = online_devices()
            self.recipes = {}
            self.tasks = []
            self.posts = []
            self.task_started = {}
            self.post_started = {}
            self.last_action = None

    def load_demo(self) -> None:
        with self.lock:
            lot = _demo_lot()
            self.recipes[DEMO_RECIPE_NAME] = _demo_recipe()
            self.tasks = [
                {
                    "task_id": DEMO_TASK_ID,
                    "task_state": 5,
                    "fetch_cubic_source": 1,
                    "fetch_cubic_state": 2,
                    "synthesis_state": 3,
                    "LOT": [self._public_lot(lot, include_small=False)],
                    "_advance_step": 5,
                }
            ]
            self.posts = [
                {
                    "post_id": DEMO_POST_ID,
                    "post_state": 6,
                    "LOT": [self._public_lot(lot, include_small=True)],
                    "_advance_step": 6,
                }
            ]
            self.task_started = {}
            self.post_started = {}
            self._task_seq = 169
            self._post_seq = 199
            self._lot_seq = 2
            self._cubic_seq = 6

    def _plc_offline(self) -> bool:
        return all(
            int(self.devices.get(key, 0)) == 0 for key in BINARY_DEVICE_KEYS
        ) and all(int(self.devices.get(key, -1)) == -1 for key in MULTISTATE_DEVICE_KEYS)

    def _next_task_id(self) -> str:
        task_id = f"TASK-{self._task_seq:06d}"
        self._task_seq += 1
        return task_id

    def _next_post_id(self) -> str:
        post_id = f"POST-{self._post_seq:06d}"
        self._post_seq += 1
        return post_id

    def _next_lot_id(self) -> str:
        date = datetime.now().strftime("%y%m%d")
        lot_id = f"LOT-{date}-{self._lot_seq:03d}"
        self._lot_seq += 1
        return lot_id

    def _next_cubic(self, prefix: str) -> str:
        code = f"{prefix}-{self._cubic_seq:03d}"
        self._cubic_seq += 1
        return code

    @staticmethod
    def _public_lot(lot: dict[str, Any], *, include_small: bool) -> dict[str, Any]:
        payload = {
            key: copy.deepcopy(value)
            for key, value in lot.items()
            if not key.startswith("_")
        }
        if not include_small:
            payload.pop("small_cubics", None)
        return payload

    def _all_lots(self) -> list[dict[str, Any]]:
        lots: dict[str, dict[str, Any]] = {}
        for task in self.tasks:
            for lot in task.get("LOT") or []:
                lot_id = str(lot.get("lotId") or "")
                if lot_id:
                    lots[lot_id] = lot
        for post in self.posts:
            for lot in post.get("LOT") or []:
                lot_id = str(lot.get("lotId") or "")
                if lot_id:
                    merged = dict(lots.get(lot_id) or {})
                    merged.update(lot)
                    lots[lot_id] = merged
        return list(lots.values())

    def _find_lot(self, lot_id: str) -> dict[str, Any] | None:
        wanted = str(lot_id or "").strip()
        if not wanted:
            return None
        for lot in self._all_lots():
            if str(lot.get("lotId") or "") == wanted:
                return lot
        return None

    def _find_bottle(self, bottle_code: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        wanted = str(bottle_code or "").strip()
        if not wanted:
            return None
        for lot in self._all_lots():
            for cubic in lot.get("small_cubics") or []:
                if str(cubic.get("bottle_code") or "") == wanted:
                    return lot, cubic
        return None

    def _find_task(self, task_id: str) -> dict[str, Any] | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        for item in self.tasks:
            if str(item.get("task_id") or "") == wanted:
                return item
        return None

    def _find_post(self, post_id: str) -> dict[str, Any] | None:
        wanted = str(post_id or "").strip()
        if not wanted:
            return None
        for item in self.posts:
            if str(item.get("post_id") or "") == wanted:
                return item
        return None

    def _other_running_task(self, task_id: str) -> bool:
        wanted = str(task_id or "").strip()
        return any(
            str(item.get("task_id") or "") != wanted
            and is_running_task_state(int(item.get("task_state") or 0))
            for item in self.tasks
        )

    def _slot_recipe(self, task: dict[str, Any], slot_num: int) -> dict[str, Any] | None:
        for item in task.get("_slots") or []:
            if int(item.get("slot_num") or 0) == slot_num:
                recipe = item.get("recipe")
                return recipe if isinstance(recipe, dict) else None
        recipes = task.get("_recipes") or []
        if 1 <= slot_num <= len(recipes) and isinstance(recipes[slot_num - 1], dict):
            return recipes[slot_num - 1]
        return None

    def _ensure_task_runtime(self, task: dict[str, Any]) -> None:
        task.setdefault("cabin_state", 0)
        slots = task.get("_slots") or []
        if "_slot_states" not in task:
            task["_slot_states"] = {
                int(item.get("slot_num") or index + 1): 0
                for index, item in enumerate(slots)
            }
        task.setdefault(
            "_acoustic",
            {
                "acoustic_resonance_upload_state": 0,
                "acoustic_resonance_fetch_state": 0,
                "acoustic_resonance_state": 0,
            },
        )

    def _ensure_sintering(self, post: dict[str, Any]) -> dict[str, Any]:
        existing = post.get("_sintering")
        if isinstance(existing, dict) and "furnace" in existing:
            return existing
        furnaces: dict[int, dict[str, Any]] = {}
        joules: list[dict[str, Any]] = []
        for lot in post.get("LOT") or []:
            for cubic in lot.get("small_cubics") or []:
                cubic_id = small_cubic_id(cubic)
                firing_type = int(cubic.get("firing_type") or 0)
                send_state = int(cubic.get("send_state") or 0)
                fetch_state = int(cubic.get("fetch_state") or 0)
                upload_state = 2 if send_state == 2 else send_state
                sintering_state = 2 if fetch_state == 2 else (1 if send_state == 2 else 0)
                if firing_type == 4:
                    joules.append(
                        {
                            "small_cubic": cubic_id,
                            "upload_state": upload_state,
                            "fetch_state": fetch_state,
                            "sintering_state": sintering_state,
                            "cooling_state": 2 if fetch_state == 2 else 0,
                        }
                    )
                    continue
                furnace_id = firing_type + 1
                entry = furnaces.setdefault(
                    furnace_id,
                    {
                        "furnace_id": furnace_id,
                        "upload_state": upload_state,
                        "fetch_state": 2,
                        "sintering_state": sintering_state,
                        "small_cubics": [],
                    },
                )
                if cubic_id:
                    entry["small_cubics"].append(cubic_id)
                if fetch_state < 2:
                    entry["fetch_state"] = 0
                if sintering_state:
                    entry["sintering_state"] = max(
                        int(entry.get("sintering_state") or 0), sintering_state
                    )
                if upload_state:
                    entry["upload_state"] = max(
                        int(entry.get("upload_state") or 0), upload_state
                    )
        payload = {
            "furnace": [furnaces[key] for key in sorted(furnaces)],
            "joule_heating": joules,
        }
        post["_sintering"] = payload
        return payload

    def _filter_ids(self, items: list[dict[str, Any]], id_key: str, from_id: str) -> list[dict[str, Any]]:
        start = str(from_id or "").strip()
        if not start:
            return items
        return [item for item in items if str(item.get(id_key) or "") >= start]

    def _update(self) -> None:
        if not self.auto_advance:
            return
        now = time.monotonic()
        for task in self.tasks:
            task_id = str(task.get("task_id") or "")
            started = self.task_started.get(task_id)
            if started is None or int(task.get("task_state") or 0) >= 5:
                continue
            step = int((now - started) / self.step_interval)
            self._advance_task(task, step)
        for post in self.posts:
            post_id = str(post.get("post_id") or "")
            started = self.post_started.get(post_id)
            if started is None or int(post.get("post_state") or 0) >= 7:
                continue
            step = int((now - started) / self.step_interval)
            self._advance_post(post, step)

    def _advance_task(self, task: dict[str, Any], step: int) -> None:
        if step <= 0:
            task["task_state"] = 1
            return
        if step == 1:
            task["task_state"] = 2
            task["fetch_cubic_state"] = 1
            return
        if step == 2:
            task["task_state"] = 2
            task["fetch_cubic_state"] = 2
            task["synthesis_state"] = 1
            return
        if step == 3:
            task["task_state"] = 3
            task["synthesis_state"] = 2
            self._ensure_lots(task, sampling=True)
            return
        if step == 4:
            task["task_state"] = 3
            task["synthesis_state"] = 3
            self._finish_sampling(task)
            return
        task["task_state"] = 4 if step == 5 else 5
        task["synthesis_state"] = 3
        self._finish_sampling(task)
        if int(task["task_state"]) == 5:
            self._ensure_post_for_task(task)

    def _ensure_lots(self, task: dict[str, Any], *, sampling: bool) -> None:
        lots = task.setdefault("LOT", [])
        recipes = task.get("_recipes") or []
        slots = task.get("_slots") or []
        cubic_type = int(task.get("_cubic_type") or 1)
        while len(lots) < len(recipes):
            index = len(lots)
            recipe = recipes[index]
            slot_num = (
                int(slots[index].get("slot_num") or index + 1)
                if index < len(slots)
                else index + 1
            )
            lots.append(
                {
                    "lotId": self._next_lot_id(),
                    "slot_num": slot_num,
                    "lot_state": 1 if sampling else 0,
                    "recipe_name": recipe.get("recipe_name"),
                    "recipe_component": recipe_component_count(recipe),
                    "recipe": _with_real_weights(recipe) if sampling else copy.deepcopy(recipe),
                    "cubic": self._next_cubic("CRU-L"),
                    "cubic_type": cubic_type,
                    "start_sampling_time": _now_text() if sampling else "",
                    "end_sampling_time": "",
                    "bottle_state": 0,
                    "bottle_time": "",
                    "acoustic_resonance_time": "",
                    "acoustic_resonance_count": 0,
                    "acoustic_resonance_params": [],
                    "small_cubics": [],
                }
            )

    def _finish_sampling(self, task: dict[str, Any]) -> None:
        self._ensure_lots(task, sampling=True)
        stamp = _now_text()
        for lot in task.get("LOT") or []:
            lot["lot_state"] = 2
            lot["end_sampling_time"] = lot.get("end_sampling_time") or stamp
            lot["acoustic_resonance_time"] = lot.get("acoustic_resonance_time") or stamp
            lot["acoustic_resonance_count"] = max(1, int(lot.get("acoustic_resonance_count") or 0))
            if not lot.get("acoustic_resonance_params"):
                lot["acoustic_resonance_params"] = [
                    {
                        "segs": [
                            {
                                "acceleration": 40,
                                "frequency": 55,
                                "id": str(uuid.uuid4()),
                                "run_time": 1100,
                            }
                        ]
                    }
                ]
        if int(task.get("task_state") or 0) >= 5:
            for lot in task.get("LOT") or []:
                lot["lot_state"] = 4
                lot["bottle_state"] = 1
                lot["bottle_time"] = lot.get("bottle_time") or stamp

    def _ensure_post_for_task(self, task: dict[str, Any]) -> None:
        lots = [copy.deepcopy(lot) for lot in task.get("LOT") or [] if lot.get("lotId")]
        if not lots:
            return
        existing_ids = {
            str(lot.get("lotId") or "")
            for post in self.posts
            for lot in post.get("LOT") or []
        }
        pending = [lot for lot in lots if str(lot.get("lotId") or "") not in existing_ids]
        if not pending:
            return
        stamp_date = datetime.now().strftime("%y%m%d")
        for lot in pending:
            lot["lot_state"] = 4
            lot["small_cubics"] = [
                {
                    "small_cubic_id": self._next_cubic("CRU-S"),
                    "create_date": stamp_date,
                    "firing_type": 0,
                    "firing_params": {
                        "open_door_temp": 160,
                        "segs": [
                            {
                                "begin_temp": 30,
                                "end_temp": 465,
                                "id": str(uuid.uuid4()),
                                "temp_time": 145,
                            }
                        ],
                    },
                    "send_state": 0,
                    "fetch_state": 0,
                    "start_firing_time": "",
                    "end_firing_time": "",
                    "scan_to_bottle_time": "",
                    "bottle_code": "",
                }
            ]
        post_id = self._next_post_id()
        self.posts.append(
            {
                "post_id": post_id,
                "post_state": 1,
                "LOT": pending,
            }
        )
        self.post_started[post_id] = time.monotonic()

    def _advance_post(self, post: dict[str, Any], step: int) -> None:
        state = min(7, max(1, step + 1))
        post["post_state"] = state
        stamp = _now_text()
        for lot in post.get("LOT") or []:
            if state <= 3:
                lot["lot_state"] = 4
            elif state == 4:
                lot["lot_state"] = 5
            elif state in {5, 6}:
                lot["lot_state"] = 6
            else:
                lot["lot_state"] = 7
            for cubic in lot.get("small_cubics") or []:
                if state >= 5:
                    cubic["send_state"] = 2
                    cubic["start_firing_time"] = cubic.get("start_firing_time") or stamp
                    self.devices["furnace_01"] = 1 if state == 5 else 2
                if state >= 6:
                    cubic["fetch_state"] = 2
                    cubic["end_firing_time"] = cubic.get("end_firing_time") or stamp
                if state >= 7:
                    cubic["scan_to_bottle_time"] = cubic.get("scan_to_bottle_time") or stamp
                    if not cubic.get("bottle_code"):
                        cubic["bottle_code"] = (
                            f"{lot.get('lotId')}-{small_cubic_id(cubic) or 'CRU-S-000'}"
                            f"-{datetime.now().strftime('%y%m%d')}"
                        )

    def _remember(self, action: Any, param: dict[str, Any], response: dict[str, Any]) -> None:
        try:
            result = int(response.get("result") or 0)
        except (TypeError, ValueError):
            result = 7
        self.last_action = {
            "action": str(action or ""),
            "result": result,
            "message": RESULT_MESSAGES.get(result, "工站返回错误"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "param_keys": list(param),
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        action = request.get("action")
        param = request.get("param") or {}
        if not isinstance(param, dict):
            param = {}
        with self.lock:
            self._update()
            response = self._handle_locked(request_id, action, param)
            self._remember(action, param, response)
            return response

    def _handle_locked(self, request_id: Any, action: Any, param: dict[str, Any]) -> dict[str, Any]:
        if action == "station_status":
            return self._response(request_id, data=dict(self.devices))
        if action == "query_tasks":
            items = self._filter_ids(self.tasks, "task_id", str(param.get("fromId") or ""))
            if bool(param.get("onlyRunning")):
                items = [
                    item
                    for item in items
                    if is_running_task_state(int(item.get("task_state") or 0))
                ]
            return self._response(
                request_id,
                data={
                    "tasks": [
                        {
                            "task_id": item["task_id"],
                            "task_state": item.get("task_state"),
                            "fetch_cubic_source": item.get("fetch_cubic_source", 1),
                            "fetch_cubic_state": item.get("fetch_cubic_state", 0),
                            "synthesis_state": item.get("synthesis_state", 0),
                            "LOT": [
                                self._public_lot(lot, include_small=False)
                                for lot in item.get("LOT") or []
                            ],
                        }
                        for item in items
                    ]
                },
            )
        if action == "query_posts":
            items = self._filter_ids(self.posts, "post_id", str(param.get("fromId") or ""))
            if bool(param.get("onlyRunning")):
                items = [
                    item
                    for item in items
                    if is_running_post_state(int(item.get("post_state") or 0))
                ]
            return self._response(
                request_id,
                data={
                    "posts": [
                        {
                            "post_id": item["post_id"],
                            "post_state": item.get("post_state"),
                            "LOT": [
                                self._public_lot(lot, include_small=True)
                                for lot in item.get("LOT") or []
                            ],
                        }
                        for item in items
                    ]
                },
            )
        if action == "query_lot":
            lot = self._find_lot(str(param.get("lotId") or ""))
            if not lot:
                return self._response(request_id, result=3)
            payload = self._public_lot(lot, include_small=True)
            payload["bottled"] = bool(int(lot.get("bottle_state") or 0) == 1 or lot.get("small_cubics"))
            return self._response(request_id, data=payload)
        if action == "query_bottle_code":
            found = self._find_bottle(str(param.get("bottle_code") or ""))
            if not found:
                return self._response(request_id, result=3)
            lot, cubic = found
            payload = self._public_lot(lot, include_small=False)
            payload["bottled"] = True
            payload["small_cubics"] = [copy.deepcopy(cubic)]
            return self._response(request_id, data=payload)
        if action == "upload_recipe":
            recipe_name = str(param.get("recipe_name") or "").strip()
            if not recipe_name or not str(param.get("formula") or "").strip():
                return self._response(request_id, result=2)
            if recipe_name in self.recipes:
                return self._response(request_id, result=4)
            recipe = copy.deepcopy(param)
            recipe["id"] = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
            self.recipes[recipe_name] = recipe
            return self._response(request_id)
        if action == "create_task":
            if self._plc_offline():
                return self._response(request_id, result=1)
            try:
                max_slots = pallet_slot_count(int(param.get("pallet_type") or 0))
                cubic_type = int(param.get("cubic_type") or 0)
            except (TypeError, ValueError):
                return self._response(request_id, result=2)
            if cubic_type not in {1, 2, 3, 4, 5}:
                return self._response(request_id, result=2)
            slots = param.get("slots")
            if not isinstance(slots, list) or not slots:
                return self._response(request_id, result=2)
            recipes: list[dict[str, Any]] = []
            slot_records: list[dict[str, Any]] = []
            for item in slots:
                if not isinstance(item, dict):
                    return self._response(request_id, result=2)
                try:
                    slot_num = int(item.get("slot_num") or 0)
                except (TypeError, ValueError):
                    return self._response(request_id, result=2)
                if slot_num < 1 or slot_num > max_slots:
                    return self._response(request_id, result=5)
                recipe_name = str(item.get("recipe_name") or "").strip()
                recipe = self.recipes.get(recipe_name)
                if not recipe:
                    return self._response(request_id, result=6)
                recipes.append(copy.deepcopy(recipe))
                slot_records.append(
                    {"slot_num": slot_num, "recipe": copy.deepcopy(recipe)}
                )
            task_id = self._next_task_id()
            task = {
                "task_id": task_id,
                "task_state": 0,
                "fetch_cubic_source": 1,
                "fetch_cubic_state": 0,
                "cabin_state": 0,
                "synthesis_state": 0,
                "LOT": [],
                "_recipes": recipes,
                "_slots": slot_records,
                "_slot_states": {item["slot_num"]: 0 for item in slot_records},
                "_acoustic": {
                    "acoustic_resonance_upload_state": 0,
                    "acoustic_resonance_fetch_state": 0,
                    "acoustic_resonance_state": 0,
                },
                "_cubic_type": cubic_type,
                "_has_bead_bottle": bool(param.get("has_bead_bottle")),
                "_bead_count": int(param.get("bead_count") or 0),
            }
            self.tasks.append(task)
            return self._response(request_id, data={"task_id": task_id})
        if action == "start_task":
            return self._handle_start_task(request_id, param)
        if action == "upload_cubic":
            return self._handle_upload_cubic(request_id, param)
        if action == "query_upload_cubic_status":
            return self._handle_query_upload_cubic_status(request_id, param)
        if action == "close_cabin_outer_door":
            return self._handle_close_cabin_outer_door(request_id, param)
        if action == "confirm_recipe":
            return self._handle_confirm_recipe(request_id, param)
        if action == "start_recipt":
            return self._handle_start_recipt(request_id, param)
        if action == "get_recipt_status":
            return self._handle_get_recipt_status(request_id, param)
        if action == "start_acoustic_resonance":
            return self._handle_start_acoustic_resonance(request_id, param)
        if action == "get_acoustic_resonance_status":
            return self._handle_get_acoustic_resonance_status(request_id, param)
        if action == "fetch_acoustic_resonance":
            return self._handle_fetch_acoustic_resonance(request_id, param)
        if action == "finish_acoustic_resonance":
            return self._handle_finish_acoustic_resonance(request_id, param)
        if action == "scan_big_cubic_to_bottle":
            return self._handle_scan_big_cubic_to_bottle(request_id, param)
        if action == "start_sintering":
            return self._handle_start_sintering(request_id, param)
        if action == "get_sintering_status":
            return self._handle_get_sintering_status(request_id, param)
        if action == "fetch_joule_heating":
            return self._handle_fetch_joule_heating(request_id, param)
        if action == "fetch_furnace":
            return self._handle_fetch_furnace(request_id, param)
        return self._response(request_id, result=2)

    def _handle_start_task(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if self._other_running_task(task_id):
            return self._response(request_id, result=8)
        if int(task.get("task_state") or 0) != 0:
            return self._response(request_id, result=9)
        task["task_state"] = 1
        if self.auto_advance:
            self.task_started[task_id] = time.monotonic()
        return self._response(request_id)

    def _handle_upload_cubic(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        task_id = str(param.get("task_id") or "").strip()
        try:
            source = int(param.get("fetch_cubic_source"))
        except (TypeError, ValueError):
            return self._response(request_id, result=2)
        if not task_id or source not in {0, 1}:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        self._ensure_task_runtime(task)
        task["fetch_cubic_source"] = source
        task["task_state"] = max(int(task.get("task_state") or 0), 2)
        if source == 0:
            task["fetch_cubic_state"] = 1
            task["cabin_state"] = 2
        else:
            task["fetch_cubic_state"] = 2
            task["cabin_state"] = 0
        return self._response(request_id)

    def _handle_query_upload_cubic_status(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        self._ensure_task_runtime(task)
        return self._response(
            request_id,
            data={
                "fetch_cubic_source": int(task.get("fetch_cubic_source") or 0),
                "fetch_cubic_state": int(task.get("fetch_cubic_state") or 0),
                "cabin_state": int(task.get("cabin_state") or 0),
            },
        )

    def _handle_close_cabin_outer_door(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        self._ensure_task_runtime(task)
        if int(task.get("fetch_cubic_source") or 0) == 0:
            task["cabin_state"] = 9
            task["fetch_cubic_state"] = 2
        return self._response(request_id)

    def _handle_confirm_recipe(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        material = str(param.get("material") or "").strip()
        try:
            slot_num = int(param.get("slot_num"))
            real_weight = float(param.get("real_weight"))
        except (TypeError, ValueError):
            return self._response(request_id, result=2)
        if not task_id or not material:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        recipe = self._slot_recipe(task, slot_num)
        if recipe is None:
            return self._response(request_id, result=10)
        spec = recipe.get(material)
        if not isinstance(spec, dict) or material in RECIPE_META_KEYS:
            return self._response(request_id, result=10)
        if not bool(spec.get("pre-add", spec.get("pre_add", False))):
            return self._response(request_id, result=11)
        spec["real_weight"] = real_weight
        return self._response(request_id)

    def _handle_start_recipt(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        self._ensure_task_runtime(task)
        task["task_state"] = max(int(task.get("task_state") or 0), 3)
        task["synthesis_state"] = 2
        self._ensure_lots(task, sampling=True)
        states = task.setdefault("_slot_states", {})
        for item in task.get("_slots") or []:
            states[int(item.get("slot_num") or 0)] = 2
        if not states:
            for index, _recipe in enumerate(task.get("_recipes") or [], start=1):
                states[index] = 2
        return self._response(request_id)

    def _handle_get_recipt_status(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        self._ensure_task_runtime(task)
        states = task.get("_slot_states") or {}
        slots = [
            {"slot_num": int(item.get("slot_num") or 0), "recipe_state": int(states.get(int(item.get("slot_num") or 0), 0))}
            for item in task.get("_slots") or []
        ]
        if not slots:
            slots = [
                {"slot_num": slot_num, "recipe_state": int(state)}
                for slot_num, state in sorted(states.items())
                if slot_num
            ]
        return self._response(request_id, data={"slots": slots})

    def _handle_start_acoustic_resonance(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        self._ensure_task_runtime(task)
        task["synthesis_state"] = 3
        task["_acoustic"] = {
            "acoustic_resonance_upload_state": 2,
            "acoustic_resonance_fetch_state": 0,
            "acoustic_resonance_state": 2,
        }
        self.devices["acoustic_resonance"] = 2
        return self._response(request_id)

    def _handle_get_acoustic_resonance_status(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        self._ensure_task_runtime(task)
        return self._response(request_id, data=dict(task["_acoustic"]))

    def _handle_fetch_acoustic_resonance(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        self._ensure_task_runtime(task)
        acoustic = task["_acoustic"]
        if (
            not self.auto_advance
            and int(acoustic.get("acoustic_resonance_upload_state") or 0) != 2
        ):
            return self._response(request_id, result=9)
        acoustic["acoustic_resonance_fetch_state"] = 2
        acoustic["acoustic_resonance_state"] = 2
        return self._response(request_id)

    def _handle_finish_acoustic_resonance(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        if not task_id:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        self._ensure_task_runtime(task)
        acoustic = task["_acoustic"]
        if (
            not self.auto_advance
            and int(acoustic.get("acoustic_resonance_fetch_state") or 0) != 2
        ):
            return self._response(request_id, result=9)
        task["synthesis_state"] = 3
        task["task_state"] = max(int(task.get("task_state") or 0), 4)
        return self._response(request_id)

    def _handle_scan_big_cubic_to_bottle(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = str(param.get("task_id") or "").strip()
        qrcode = str(param.get("qrcode") or "").strip()
        if not task_id or not qrcode:
            return self._response(request_id, result=2)
        task = self._find_task(task_id)
        if task is None:
            return self._response(request_id, result=7)
        if int(task.get("task_state") or 0) < 1:
            return self._response(request_id, result=9)
        stamp = _now_text()
        matched = False
        for lot in task.get("LOT") or []:
            if str(lot.get("cubic") or "") == qrcode:
                lot["bottle_state"] = 1
                lot["bottle_time"] = lot.get("bottle_time") or stamp
                lot["lot_state"] = 4
                matched = True
        if not matched:
            return self._response(request_id, result=12)
        return self._response(request_id)

    def _handle_start_sintering(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        post_id = str(param.get("post_id") or "").strip()
        mode = str(param.get("joule_heating_fetch_mode") or "auto").strip().lower()
        if not post_id:
            return self._response(request_id, result=2)
        if mode not in {"auto", "manual"}:
            return self._response(request_id, result=2)
        post = self._find_post(post_id)
        if post is None:
            return self._response(request_id, result=7)
        if int(post.get("post_state") or 0) >= 7:
            return self._response(request_id, result=9)
        post["_joule_heating_fetch_mode"] = mode
        self._ensure_sintering(post)
        post["post_state"] = max(int(post.get("post_state") or 0), 5)
        return self._response(request_id)

    def _handle_get_sintering_status(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        post_id = str(param.get("post_id") or "").strip()
        if not post_id:
            return self._response(request_id, result=2)
        post = self._find_post(post_id)
        if post is None:
            return self._response(request_id, result=7)
        if int(post.get("post_state") or 0) < 1:
            return self._response(request_id, result=9)
        return self._response(request_id, data=copy.deepcopy(self._ensure_sintering(post)))

    def _handle_fetch_joule_heating(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        post_id = str(param.get("post_id") or "").strip()
        if not post_id:
            return self._response(request_id, result=2)
        post = self._find_post(post_id)
        if post is None:
            return self._response(request_id, result=7)
        if int(post.get("post_state") or 0) < 1:
            return self._response(request_id, result=9)
        sintering = self._ensure_sintering(post)
        pending = next(
            (
                item
                for item in sintering.get("joule_heating") or []
                if int(item.get("fetch_state") or 0) < 2
            ),
            None,
        )
        if pending is None:
            return self._response(request_id, result=13)
        pending["fetch_state"] = 2
        pending["sintering_state"] = 2
        pending["cooling_state"] = 2
        return self._response(request_id)

    def _handle_fetch_furnace(
        self, request_id: Any, param: dict[str, Any]
    ) -> dict[str, Any]:
        if self._plc_offline():
            return self._response(request_id, result=1)
        post_id = str(param.get("post_id") or "").strip()
        try:
            furnace_id = int(param.get("furnace_id"))
        except (TypeError, ValueError):
            return self._response(request_id, result=2)
        if not post_id or furnace_id not in {1, 2, 3, 4}:
            return self._response(request_id, result=2)
        post = self._find_post(post_id)
        if post is None:
            return self._response(request_id, result=7)
        if int(post.get("post_state") or 0) < 1:
            return self._response(request_id, result=9)
        sintering = self._ensure_sintering(post)
        furnace = next(
            (
                item
                for item in sintering.get("furnace") or []
                if int(item.get("furnace_id") or 0) == furnace_id
            ),
            None,
        )
        if furnace is None or int(furnace.get("fetch_state") or 0) >= 2:
            return self._response(request_id, result=13)
        if not furnace.get("small_cubics"):
            return self._response(request_id, result=13)
        furnace["fetch_state"] = 2
        furnace["sintering_state"] = 2
        return self._response(request_id)

    @staticmethod
    def _response(
        request_id: Any, result: int = 0, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response: dict[str, Any] = {"request_id": request_id, "result": result}
        if data is not None:
            response["data"] = data
        return response


class _Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        server: MockSynthesisServer = self.server  # type: ignore[assignment]
        with server.client_lock:
            server.client_count += 1
            server.last_client = f"{self.client_address[0]}:{self.client_address[1]}"
            server.state.last_client = server.last_client

    def finish(self) -> None:
        server: MockSynthesisServer = self.server  # type: ignore[assignment]
        with server.client_lock:
            server.client_count = max(0, server.client_count - 1)
        super().finish()

    def handle(self) -> None:
        state: MockSynthesisState = self.server.state  # type: ignore[attr-defined]
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            request: dict[str, Any] | None = None
            try:
                parsed = json.loads(raw.decode("utf-8").strip())
                if not isinstance(parsed, dict):
                    raise ValueError("请求必须是 JSON 对象")
                request = parsed
                response = state.handle(request)
            except Exception as exc:  # noqa: BLE001
                request_id = request.get("request_id") if isinstance(request, dict) else None
                response = {"request_id": request_id, "result": 2, "message": str(exc)}
            self.wfile.write(encode_station_response(response))
            self.wfile.flush()


class MockSynthesisServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int] = ("127.0.0.1", 19101),
        *,
        state: MockSynthesisState | None = None,
    ) -> None:
        self.state = state or MockSynthesisState()
        self.client_lock = threading.Lock()
        self.client_count = 0
        self.last_client = ""
        super().__init__(address, _Handler)


class MockSynthesisRuntime:
    """TCP Mock 及其可选 HTTP 交互页。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 19101,
        *,
        state: MockSynthesisState | None = None,
        ui_host: str = "127.0.0.1",
        ui_port: int = 0,
        enable_ui: bool = False,
    ) -> None:
        self.state = state or MockSynthesisState()
        self._ui_host = ui_host
        self._enable_ui = enable_ui
        self._lock = threading.Lock()
        self.tcp_server = MockSynthesisServer((host, port), state=self.state)
        self.http_server: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []
        if enable_ui:
            self.http_server = ThreadingHTTPServer(
                (ui_host, ui_port), _make_ui_handler(self)
            )

    @property
    def host(self) -> str:
        return str(self.tcp_server.server_address[0])

    @property
    def port(self) -> int:
        return int(self.tcp_server.server_address[1])

    @property
    def ui_port(self) -> int:
        if self.http_server is None:
            return 0
        return int(self.http_server.server_address[1])

    def start(self) -> None:
        tcp_thread = threading.Thread(
            target=self.tcp_server.serve_forever,
            name="mock-synthesis-tcp",
            daemon=True,
        )
        tcp_thread.start()
        self._threads.append(tcp_thread)
        if self.http_server is not None:
            http_thread = threading.Thread(
                target=self.http_server.serve_forever,
                name="mock-synthesis-ui",
                daemon=True,
            )
            http_thread.start()
            self._threads.append(http_thread)

    def rebind(self, host: str, port: int) -> tuple[str, int]:
        with self._lock:
            old = self.tcp_server
            old.shutdown()
            old.server_close()
            self.tcp_server = MockSynthesisServer((host, port), state=self.state)
            thread = threading.Thread(
                target=self.tcp_server.serve_forever,
                name="mock-synthesis-tcp",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
            return self.host, self.port

    def close(self) -> None:
        self.tcp_server.shutdown()
        self.tcp_server.server_close()
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()

    def status_payload(self) -> dict[str, Any]:
        snap = self.state.snapshot()
        return {
            "tcp_host": self.host,
            "tcp_port": self.port,
            "ui_port": self.ui_port,
            "listening": True,
            "client_count": self.tcp_server.client_count,
            "last_client": snap.get("last_client") or self.tcp_server.last_client,
            **snap,
        }


def _make_ui_handler(runtime: MockSynthesisRuntime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8", code)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(value, dict):
                raise ValueError("请求体必须是对象")
            return value

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send(_ui_page().encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/state":
                self._json(runtime.status_payload())
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/device":
                    body = self._read_json()
                    runtime.state.set_device(
                        str(body.get("key") or ""),
                        int(body.get("value") if body.get("value") is not None else (1 if body.get("online") else 0)),
                    )
                    self._json(runtime.status_payload())
                    return
                if path == "/api/bind":
                    body = self._read_json()
                    host = str(body.get("host") or runtime.host)
                    port = int(body.get("port") or runtime.port)
                    runtime.rebind(host, port)
                    self._json(runtime.status_payload())
                    return
                if path == "/api/reset":
                    runtime.state.reset()
                    self._json(runtime.status_payload())
                    return
                if path == "/api/demo":
                    runtime.state.load_demo()
                    self._json(runtime.status_payload())
                    return
                if path == "/api/auto":
                    body = self._read_json()
                    runtime.state.set_auto_advance(bool(body.get("enabled")))
                    self._json(runtime.status_payload())
                    return
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 400)
                return
            self._json({"error": "not found"}, 404)

    return Handler


def _ui_page() -> str:
    device_rows = "".join(
        f'<label>{DEVICE_LABELS[key]} <small>{key}</small> '
        f'<input type="number" data-device="{key}" min="-1" max="2"></label>'
        for key in DEVICE_KEYS
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>合成工站 Mock</title>
  <style>
    :root {{ color-scheme: dark; font-family: "Microsoft YaHei", sans-serif; }}
    body {{ margin: 0; background: #0d1524; color: #e8eef8; }}
    header {{ padding: 16px 24px; background: #132038; border-bottom: 1px solid #25324a; }}
    main {{ max-width: 980px; margin: 20px auto; padding: 0 16px 48px; }}
    .card {{ background: #101b2e; border: 1px solid #25324a; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
    input[type=text], input[type=number] {{ background: #0d1524; color: #e8eef8; border: 1px solid #314a63; padding: 6px 8px; border-radius: 6px; width: 72px; }}
    button.action {{ background: #2d6cdf; color: white; border: 0; border-radius: 8px; padding: 8px 12px; cursor: pointer; }}
    .ok {{ color: #72e6a1; }} .warn {{ color: #ffd88a; }} .err {{ color: #ff8a8a; }}
  </style>
</head>
<body>
  <header>
    <h1>合成工站虚拟服务</h1>
    <p id="listen">监听中…</p>
  </header>
  <main>
    <section class="card">
      <h2>连接</h2>
      <div class="row">
        <label>地址 <input id="host" type="text" style="width:140px"></label>
        <label>端口 <input id="port" type="number"></label>
        <button class="action" id="bind">重新绑定并确认监听</button>
        <span id="clients"></span>
      </div>
      <p id="last-action"></p>
    </section>
    <section class="card">
      <h2>任务</h2>
      <p id="work-line">无任务。</p>
      <p class="row">
        <label><input type="checkbox" id="auto-advance"> 自动推进 TASK / POST</label>
      </p>
    </section>
    <section class="card">
      <h2>设备状态</h2>
      <p>二元设备 0/1；声共振 / 焦耳热 / 马弗炉 -1/0/1/2。</p>
      <div class="row" id="devices">{device_rows}</div>
    </section>
    <section class="card row">
      <button class="action" id="demo">装入演示配方与 TASK/POST</button>
      <button class="action" id="reset">初始化 / 清零</button>
    </section>
  </main>
  <script>
    async function load() {{
      const state = await (await fetch("/api/state")).json();
      document.getElementById("host").value = state.tcp_host;
      document.getElementById("port").value = state.tcp_port;
      document.getElementById("listen").innerHTML =
        'TCP <span class="ok">' + state.tcp_host + ':' + state.tcp_port + '</span> 已监听';
      const clients = document.getElementById("clients");
      if (state.client_count) {{
        clients.textContent = 'UniLab 在线 ' + state.last_client;
        clients.className = "ok";
      }} else if (state.last_client || (state.last_action && state.last_action.time)) {{
        clients.textContent = '最近客户端 ' + (state.last_client || '已通信');
        clients.className = "ok";
      }} else {{
        clients.textContent = '尚无客户端';
        clients.className = "warn";
      }}
      const last = state.last_action;
      const lastEl = document.getElementById("last-action");
      if (last) {{
        lastEl.textContent = last.time + '  ' + last.action +
          '  result=' + last.result + '  ' + (last.message || '');
        lastEl.className = Number(last.result) === 0 ? "ok" : "err";
      }} else {{
        lastEl.textContent = '';
      }}
      const work = document.getElementById("work-line");
      work.textContent = '配方 ' + (state.recipe_names || []).length +
        ' · TASK ' + state.task_count +
        ' · POST ' + state.post_count +
        ' · 运行 TASK ' + ((state.running_tasks || [])[0] || '-') +
        ' · 运行 POST ' + ((state.running_posts || [])[0] || '-');
      const autoBox = document.getElementById("auto-advance");
      if (autoBox && document.activeElement !== autoBox) {{
        autoBox.checked = Boolean(state.auto_advance);
      }}
      for (const box of document.querySelectorAll("[data-device]")) {{
        if (document.activeElement !== box) {{
          box.value = Number(state.devices[box.dataset.device] || 0);
        }}
      }}
    }}
    document.getElementById("devices").addEventListener("change", async (event) => {{
      const box = event.target;
      if (!box.dataset.device) return;
      await fetch("/api/device", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{key: box.dataset.device, value: Number(box.value)}})
      }});
      load();
    }});
    document.getElementById("bind").onclick = async () => {{
      await fetch("/api/bind", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{
          host: document.getElementById("host").value,
          port: Number(document.getElementById("port").value)
        }})
      }});
      load();
    }};
    document.getElementById("demo").onclick = async () => {{
      await fetch("/api/demo", {{method: "POST"}});
      load();
    }};
    document.getElementById("reset").onclick = async () => {{
      await fetch("/api/reset", {{method: "POST"}});
      load();
    }};
    document.getElementById("auto-advance").onchange = async (event) => {{
      await fetch("/api/auto", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{enabled: event.target.checked}})
      }});
      load();
    }};
    load();
    setInterval(load, 2000);
  </script>
</body>
</html>"""


def start_mock_in_process(
    host: str = "127.0.0.1",
    port: int = 19101,
    *,
    state: MockSynthesisState | None = None,
    enable_ui: bool = False,
    ui_port: int = 0,
) -> MockSynthesisRuntime:
    """在后台线程启动 TCP Mock，供 SynthesisStation(use_mock=True) 使用。"""
    runtime = MockSynthesisRuntime(
        host,
        port,
        state=state,
        enable_ui=enable_ui,
        ui_port=ui_port,
    )
    runtime.start()
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="合成工站 TCP 协议模拟服务端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19101)
    parser.add_argument("--ui-host", default="127.0.0.1")
    parser.add_argument("--ui-port", type=int, default=19102)
    parser.add_argument("--no-ui", action="store_true")
    parser.add_argument("--demo", action="store_true", help="启动时装入演示配方与 TASK/POST")
    parser.add_argument("--step-interval", type=float, default=0.5)
    parser.add_argument(
        "--no-auto-advance",
        action="store_true",
        help="关闭自动推进，便于逐步观察；默认开启",
    )
    args = parser.parse_args()
    state = MockSynthesisState(
        step_interval=args.step_interval,
        auto_advance=not args.no_auto_advance,
        load_demo=args.demo,
    )
    runtime = start_mock_in_process(
        args.host,
        args.port,
        state=state,
        enable_ui=not args.no_ui,
        ui_port=args.ui_port,
    )
    listen = f"模拟合成工站 TCP {runtime.host}:{runtime.port}"
    if runtime.ui_port:
        listen += f"  交互页 http://{args.ui_host}:{runtime.ui_port}/"
    print(listen, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
