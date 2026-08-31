"""合成工站协议常量：设备字段、任务状态、错误码。"""

from __future__ import annotations

import json
from typing import Any

from yb_sse_devices.synthesis_types import RecipeMaterial, TaskSlot

# 二元设备：0 离线 / 1 在线。
BINARY_DEVICE_KEYS: tuple[str, ...] = (
    "robot_arm",
    "scanner",
    "bead_device",
)

# 多态设备：-1 离线 / 0 空闲 / 1 运行 / 2 完成。
MULTISTATE_DEVICE_KEYS: tuple[str, ...] = (
    "acoustic_resonance",
    "joule_heating",
    "furnace_01",
    "furnace_02",
    "furnace_03",
    "furnace_04",
)

DEVICE_KEYS: tuple[str, ...] = BINARY_DEVICE_KEYS + MULTISTATE_DEVICE_KEYS

DEVICE_LABELS: dict[str, str] = {
    "robot_arm": "机械臂",
    "scanner": "扫码器",
    "bead_device": "加珠仪",
    "acoustic_resonance": "声共振",
    "joule_heating": "焦耳热",
    "furnace_01": "马弗炉1",
    "furnace_02": "马弗炉2",
    "furnace_03": "马弗炉3",
    "furnace_04": "马弗炉4",
}

TASK_STATE_LABELS: dict[int, str] = {
    0: "未开始",
    1: "已就绪",
    2: "取坩埚阶段",
    3: "合成阶段",
    4: "装瓶阶段",
    5: "完成",
}

POST_STATE_LABELS: dict[int, str] = {
    0: "未开始",
    1: "已就绪",
    2: "扫瓶组批",
    3: "扫瓶分料",
    4: "排程",
    5: "烧结",
    6: "装瓶入库",
    7: "完成",
}

LOT_STATE_LABELS: dict[int, str] = {
    0: "等待加样",
    1: "加样中",
    2: "加样成功",
    3: "加样失败",
    4: "已装瓶 / 等待处理",
    5: "已排程，等待烧结",
    6: "烧结中",
    7: "已完成",
}

FETCH_CUBIC_STATE_LABELS: dict[int, str] = {
    0: "未开始",
    1: "进行中",
    2: "已完成",
}

SYNTHESIS_STATE_LABELS: dict[int, str] = {
    0: "未开始",
    1: "加样确认",
    2: "加样",
    3: "声共振",
}

SEND_STATE_LABELS: dict[int, str] = {
    0: "等待",
    1: "送烧中",
    2: "送烧成功",
    3: "送烧失败",
}

FETCH_STATE_LABELS: dict[int, str] = {
    0: "等待",
    1: "下坩埚中",
    2: "下坩埚成功",
    3: "下坩埚失败",
}

PALLET_SLOT_COUNTS: dict[int, int] = {1: 4, 2: 5, 3: 6}

CUBIC_TYPE_LABELS: dict[int, str] = {
    1: "Al2O3 30*30",
    2: "Al2O3 35*40",
    3: "Al2O3 40*40",
    4: "ZrO2 40*35",
    5: "ZrO2 40*46",
}

FIRING_TYPE_LABELS: dict[int, str] = {
    0: "马弗炉1烧结",
    1: "马弗炉2烧结",
    2: "马弗炉3烧结",
    3: "马弗炉4烧结",
    4: "焦耳热烧结",
}

RECIPE_META_KEYS: frozenset[str] = frozenset(
    {"recipe_name", "formula", "synthesis_mass", "n_ball_bead", "id"}
)

HEALTH_LABELS: dict[str, str] = {
    "IDLE": "空闲",
    "BUSY": "运行中",
    "FAULT": "故障",
    "OFFLINE": "离线",
}

RESULT_MESSAGES: dict[int, str] = {
    0: "成功",
    1: "PLC 未连接",
    2: "请求参数错误",
    3: "找不到相应 LOT 或者装瓶二维码数据",
    4: "配方名称重复",
    5: "槽位超出托盘能够放置的范围",
    6: "找不到相应的配方",
    7: "找不到相应的 TASK 或 POST",
    8: "有别的 TASK 正在运行",
    9: "错误的 TASK/POST 状态，无法执行当前操作",
    10: "找不到相应的物料",
    11: "物料不是掺杂剂，非预加",
    12: "找不到该大坩埚",
    13: "没有需要下的小坩埚",
}

DECK_ICON = "synthesis_station.webp"

DEFAULT_RECIPE_MATERIALS: list[RecipeMaterial] = [
    {"name": "Li2S", "weight": 0.9, "tolerance": 0.0007, "pre_add": False},
    {"name": "LiBr", "weight": 0.87, "tolerance": 0.0005, "pre_add": True},
    {"name": "LiCl", "weight": 1.2, "tolerance": 0.001, "pre_add": False},
    {"name": "P2S5", "weight": 0.88, "tolerance": 0.0005, "pre_add": False},
]


def normalize_recipe_materials(materials: Any) -> dict[str, Any]:
    """接受列表、字典或 JSON 字符串，转成 {物料名: {weight, tolerance, pre_add}}。"""
    raw: Any = materials
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        raw = json.loads(raw) if raw.strip() else {}
    if isinstance(raw, dict):
        items = []
        for name, spec in raw.items():
            if isinstance(spec, dict):
                item = dict(spec)
                item.setdefault("name", name)
                items.append(item)
            else:
                raise ValueError(f"物料 {name} 必须是对象")
        raw = items
    if not isinstance(raw, list):
        raise ValueError("materials 必须是物料列表或对象")
    snapshot: dict[str, Any] = {}
    for spec in raw:
        if not isinstance(spec, dict):
            raise ValueError("materials 每一项必须是对象")
        key = str(spec.get("name") or spec.get("material") or "").strip()
        if not key or key in RECIPE_META_KEYS:
            continue
        snapshot[key] = {
            "weight": spec.get("weight"),
            "tolerance": spec.get("tolerance"),
            "pre_add": spec.get("pre_add", spec.get("pre-add", False)),
        }
    return snapshot


def protocol_error_message(result: int) -> str:
    code = int(result)
    text = RESULT_MESSAGES.get(code, "工站返回错误")
    return f"{text} (result={code})"


def pallet_slot_count(pallet_type: int) -> int:
    try:
        count = PALLET_SLOT_COUNTS[int(pallet_type)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("pallet_type 必须是 1（4 槽）、2（5 槽）或 3（6 槽）") from exc
    return count


def online_devices() -> dict[str, int]:
    snapshot = {key: 1 for key in BINARY_DEVICE_KEYS}
    snapshot.update({key: 0 for key in MULTISTATE_DEVICE_KEYS})
    return snapshot


def offline_devices() -> dict[str, int]:
    snapshot = {key: 0 for key in BINARY_DEVICE_KEYS}
    snapshot.update({key: -1 for key in MULTISTATE_DEVICE_KEYS})
    return snapshot


def _device_offline(key: str, value: int) -> bool:
    if key in BINARY_DEVICE_KEYS:
        return int(value) == 0
    return int(value) == -1


def device_is_running(devices: dict[str, int] | None) -> bool:
    if not devices:
        return False
    return any(int(devices.get(key, 0) or 0) == 1 for key in MULTISTATE_DEVICE_KEYS)


def is_running_task_state(state: int) -> bool:
    return 1 <= int(state) <= 4


def is_running_post_state(state: int) -> bool:
    return 1 <= int(state) <= 6


def summarize_station_health(
    devices: dict[str, int] | None,
    *,
    connected: bool,
    work_running: bool = False,
) -> str:
    """看板可识别：IDLE / BUSY / FAULT / OFFLINE。"""
    if not connected:
        return "OFFLINE"
    if not devices:
        return "OFFLINE"
    values = {key: int(devices.get(key, -1 if key in MULTISTATE_DEVICE_KEYS else 0)) for key in DEVICE_KEYS}
    offlines = [_device_offline(key, values[key]) for key in DEVICE_KEYS]
    if all(offlines):
        return "OFFLINE"
    if any(offlines):
        return "FAULT"
    if work_running or device_is_running(values):
        return "BUSY"
    return "IDLE"


def materials_from_columns(
    names: list[str] | None,
    weights: list[float] | None,
    tolerances: list[float] | None,
    pre_adds: list[bool] | None,
) -> list[dict[str, Any]]:
    """把工作流里的四列基本类型列表拼成物料行。"""
    if not names:
        return [dict(item) for item in DEFAULT_RECIPE_MATERIALS]
    weight_values = list(weights or [])
    tolerance_values = list(tolerances or [])
    pre_add_values = list(pre_adds or [])
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        rows.append(
            {
                "name": name,
                "weight": weight_values[index] if index < len(weight_values) else 0.0,
                "tolerance": (
                    tolerance_values[index] if index < len(tolerance_values) else 0.0
                ),
                "pre_add": (
                    bool(pre_add_values[index]) if index < len(pre_add_values) else False
                ),
            }
        )
    return rows


def slots_from_columns(
    slot_nums: list[int] | None,
    recipe_names: list[str] | None,
) -> list[dict[str, Any]]:
    """把工作流里的槽位号 / 配方名两列拼成 TASK slots。"""
    numbers = list(slot_nums or [])
    names = list(recipe_names or [])
    if not numbers and not names:
        return [
            {"slot_num": 1, "recipe_name": "260708-Li6PS5Cl-R1"},
            {"slot_num": 2, "recipe_name": "260708-Li6PS5Cl-R1"},
        ]
    count = max(len(numbers), len(names))
    rows: list[dict[str, Any]] = []
    for index in range(count):
        rows.append(
            {
                "slot_num": numbers[index] if index < len(numbers) else index + 1,
                "recipe_name": names[index] if index < len(names) else "",
            }
        )
    return rows


def _has_text_items(values: Any) -> bool:
    return any(str(item).strip() for item in (values or []))


def resolve_recipe_materials_input(
    powder_names: list[str] | None,
    powder_weights: list[float] | None,
    powder_tolerances: list[float] | None,
    powder_pre_adds: list[bool] | None,
    leftover: Any = None,
) -> list[dict[str, Any]]:
    """优先用新列；旧工作流仍传 materials 时回退。"""
    if _has_text_items(powder_names):
        return materials_from_columns(
            powder_names, powder_weights, powder_tolerances, powder_pre_adds
        )
    normalized = normalize_recipe_materials(leftover)
    if normalized:
        return [
            {
                "name": name,
                "weight": spec.get("weight") or 0.0,
                "tolerance": spec.get("tolerance") or 0.0,
                "pre_add": bool(spec.get("pre_add")),
            }
            for name, spec in normalized.items()
        ]
    return materials_from_columns(None, None, None, None)


def resolve_task_slots_input(
    slot_nums: list[int] | None,
    recipe_names: list[str] | None,
    leftover: Any = None,
) -> list[dict[str, Any]]:
    """优先用新列；旧工作流仍传 slots 时回退。"""
    if slot_nums or _has_text_items(recipe_names):
        return slots_from_columns(slot_nums, recipe_names)
    if isinstance(leftover, list):
        rows: list[dict[str, Any]] = []
        for item in leftover:
            if not isinstance(item, dict):
                continue
            slot_num = item.get("slot_num", item.get("slotNum"))
            recipe_name = str(
                item.get("recipe_name") or item.get("recipeName") or ""
            ).strip()
            if slot_num in (None, "") and not recipe_name:
                continue
            rows.append({"slot_num": slot_num, "recipe_name": recipe_name})
        if rows:
            return rows
    return slots_from_columns(None, None)


def flatten_recipe_param(
    recipe_name: str,
    formula: str,
    synthesis_mass: float,
    n_ball_bead: int,
    materials: Any,
) -> dict[str, Any]:
    """把 UniLab 蛇形参数展平为协议 upload_recipe 顶层字段。"""
    param: dict[str, Any] = {
        "recipe_name": str(recipe_name).strip(),
        "formula": str(formula).strip(),
        "synthesis_mass": float(synthesis_mass),
        "n_ball_bead": int(n_ball_bead),
    }
    for name, spec in normalize_recipe_materials(materials).items():
        param[name] = {
            "weight": float(spec.get("weight") or 0),
            "tolerance": float(spec.get("tolerance") or 0),
            "pre-add": bool(spec.get("pre_add", False)),
        }
    return param


def recipe_component_count(recipe: dict[str, Any] | None) -> int:
    return sum(1 for key, value in (recipe or {}).items() if key not in RECIPE_META_KEYS and isinstance(value, dict))


def small_cubic_id(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    return str(item.get("small_cubic_id") or item.get("id") or "").strip()
