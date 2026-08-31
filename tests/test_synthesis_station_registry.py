from __future__ import annotations

import ast
from pathlib import Path

from unilabos.registry.decorators import (
    get_action_meta,
    get_device_meta,
    get_resource_meta,
    get_topic_config,
    is_not_action,
)

from yb_sse_devices.synthesis_protocol import DECK_ICON
from yb_sse_devices.synthesis_types import RecipeMaterial, TaskSlot


ROOT = Path(__file__).resolve().parents[1]
DEVICE_DIR = ROOT / "yb_sse_devices"


def test_synthesis_station_actions_are_discoverable_by_registry() -> None:
    tree = ast.parse((DEVICE_DIR / "synthesis_station.py").read_text(encoding="utf-8"))
    device_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SynthesisStation"
    )
    device_decorator = next(
        decorator
        for decorator in device_class.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "device"
    )
    device_id = next(
        keyword.value.value
        for keyword in device_decorator.keywords
        if keyword.arg == "id" and isinstance(keyword.value, ast.Constant)
    )
    # 注册表按 AST 上传 icon；跨模块常量会变成 module:NAME，必须是字面量。
    icon = next(
        keyword.value.value
        for keyword in device_decorator.keywords
        if keyword.arg == "icon" and isinstance(keyword.value, ast.Constant)
    )
    assert icon == "synthesis_station.webp"
    actions = {
        method.name
        for method in device_class.body
        if isinstance(method, ast.FunctionDef)
        and any(
            (isinstance(decorator, ast.Name) and decorator.id == "action")
            or (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "action"
            )
            for decorator in method.decorator_list
        )
    }
    init_method = next(
        method
        for method in device_class.body
        if isinstance(method, ast.FunctionDef) and method.name == "__init__"
    )

    assert device_id == "synthesis_station"
    assert actions == {
        "query_tasks",
        "query_posts",
        "station_status",
        "query_lot",
        "query_bottle_code",
        "test_connection",
        "upload_recipe",
        "create_task",
        "start_task",
        "upload_cubic",
        "query_upload_cubic_status",
        "close_cabin_outer_door",
        "confirm_recipe",
        "start_recipt",
        "get_recipt_status",
        "start_acoustic_resonance",
        "get_acoustic_resonance_status",
        "fetch_acoustic_resonance",
        "finish_acoustic_resonance",
        "scan_big_cubic_to_bottle",
        "start_sintering",
        "get_sintering_status",
        "fetch_joule_heating",
        "fetch_furnace",
    }
    assert {arg.arg for arg in init_method.args.args if arg.arg != "self"} == {
        "device_id",
        "config",
        "ip",
        "port",
        "connect_timeout",
        "response_timeout",
        "max_message_bytes",
        "encoding",
        "frame_delimiter",
        "station_action_names",
        "use_mock",
        "status_poll_interval",
    }


def test_template_style_config_is_supported() -> None:
    from yb_sse_devices import SynthesisStation

    station = SynthesisStation(
        device_id="SYNTHESIS_STATION_1",
        config={
            "ip": "192.0.2.20",
            "port": 20002,
            "response_timeout": 30,
            "station_action_names": {"station_status": "status"},
        },
    )

    assert station.device_id == "SYNTHESIS_STATION_1"
    assert station.ip == "192.0.2.20"
    assert station.port == 20002
    assert station.response_timeout == 30
    assert station.station_action_names == {"station_status": "status"}
    assert station.status == "OFFLINE"


def test_status_properties_are_topics() -> None:
    from yb_sse_devices import SynthesisStation

    topic_names = (
        "status",
        "station_health",
        "connected",
        "robot_arm",
        "scanner",
        "bead_device",
        "acoustic_resonance",
        "joule_heating",
        "furnace_01",
        "furnace_02",
        "furnace_03",
        "furnace_04",
        "running_task_count",
        "running_post_count",
        "current_task_id",
        "current_post_id",
    )
    for name in topic_names:
        attr = getattr(SynthesisStation, name)
        assert get_topic_config(attr.fget) != {}


def test_template_decorator_metadata_is_registered() -> None:
    from yb_sse_devices import SynthesisStation, SynthesisStation_Deck

    device_meta = get_device_meta(SynthesisStation)
    assert device_meta is not None
    assert device_meta["device_id"] == "synthesis_station"
    assert device_meta["displayname"] == "合成工站"
    assert device_meta["icon"] == DECK_ICON
    assert get_action_meta(SynthesisStation.station_status)["always_free"] is True
    assert get_action_meta(SynthesisStation.get_recipt_status)["always_free"] is True
    create_handles = get_action_meta(SynthesisStation.create_task)["handles"]
    assert create_handles["output"][0]["handler_key"] == "task_id"
    assert create_handles["output"][0]["data_key"] == "task_id"
    start_handles = get_action_meta(SynthesisStation.start_task)["handles"]
    assert start_handles["input"][0]["handler_key"] == "task_id"
    assert start_handles["output"][0]["handler_key"] == "task_id"
    assert is_not_action(SynthesisStation.close)

    resource_meta = get_resource_meta(SynthesisStation_Deck)
    assert resource_meta is not None
    assert resource_meta["resource_id"] == "SynthesisStation_Deck"
    assert resource_meta["icon"] == DECK_ICON


def test_slot_and_material_typeddict_fields_are_concrete() -> None:
    assert set(TaskSlot.__annotations__) == {"slot_num", "recipe_name"}
    assert set(RecipeMaterial.__annotations__) == {
        "name",
        "weight",
        "tolerance",
        "pre_add",
    }
    assert TaskSlot.__annotations__["slot_num"] is int
    assert RecipeMaterial.__annotations__["weight"] is float


def _action_arg_names(method_name: str) -> set[str]:
    tree = ast.parse((DEVICE_DIR / "synthesis_station.py").read_text(encoding="utf-8"))
    device_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SynthesisStation"
    )
    method = next(
        node
        for node in device_class.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    return {arg.arg for arg in method.args.args if arg.arg != "self"}


def test_workflow_lists_are_primitive_columns() -> None:
    assert _action_arg_names("upload_recipe") == {
        "recipe_name",
        "formula",
        "synthesis_mass",
        "n_ball_bead",
        "powder_names",
        "powder_weights",
        "powder_tolerances",
        "powder_pre_adds",
    }
    assert _action_arg_names("create_task") == {
        "pallet_type",
        "cubic_type",
        "task_slot_nums",
        "task_recipe_names",
        "has_bead_bottle",
        "bead_count",
    }
