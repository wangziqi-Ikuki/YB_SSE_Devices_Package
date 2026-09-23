"""Converted legacy workflow: powder and bead loading from rack 2."""

from typing import Literal, TypedDict

from unilabos.registry.annotations import JSONValue
from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.experiment_operations.synthesis_load_tray import (
    synthesis_load_tray,
)
from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)


class LegacyTaskResult(TypedDict):
    task_id: str
    # 每个任务槽位的 PLC CMD_SAMPLE 完成结果。每项包含 slot_num、
    # status_code/status_name、weights、result_codes、accepted/success、
    # qr_code 和 message 等字段，直接对应 PLC 反馈和上位机显示结果。
    sampling_results: list[dict[str, JSONValue]]


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="2b618ad2-cb04-4ce0-8f0f-4155497740b2",
    displayname="合成工站 加粉加珠流程 料架2",
    description=(
        "启动后先由人工将托盘和坩埚放到料架2并确认；确认通过后，"
        "PLC命令7执行上托盘到料架1，随后命令3完成扫码、天平开关门、"
        "称粉/加珠并按指定位置返回坩埚。"
    ),
)
def synthesis_powder_bead_rack2(
    *,
    # Operator-facing values are package-owned labels.  The device action
    # resolves them to PLC integer codes; no PLC register fields are exposed
    # in this workflow contract.
    recipe_name: Literal["YB-LiCl-P2S5-2G", "YB-SIM-Li6PS5Cl"] = "YB-LiCl-P2S5-2G",
    pallet_type: Literal["4 槽位托盘", "5 槽位托盘", "6 槽位托盘"] = "6 槽位托盘",
    cubic_type: Literal[
        "Al2O3 30*30",
        "Al2O3 35*40",
        "Al2O3 40*40",
        "ZrO2 40*35",
        "ZrO2 40*46",
    ] = "Al2O3 30*30",
    task_slot_nums: list[int],
    has_bead_bottle: bool = False,
    bead_count: int = 0,
) -> LegacyTaskResult:
    # unilab:node_uuid=ab618ad2-cb04-4ce0-8f0f-4155497740b2
    created = station.create_batch(
        recipe_name=recipe_name,
        pallet_type=pallet_type,
        cubic_type=cubic_type,
        task_slot_nums=task_slot_nums,
        has_bead_bottle=has_bead_bottle,
        bead_count=bead_count,
        # The workflow is specifically the rack-2 route.  These are station
        # implementation details rather than operator inputs.
        fetch_cubic_source=1,
    )

    # 上托盘实验操作内部带人工确认：操作员先将托盘和坩埚放到料架2，
    # 确认通过后才启动 PLC 命令7（威纶通“上托盘流程启动”）。
    # unilab:node_uuid=4c1a8f6e-0d55-4e67-9f0c-5eb22ab87f7c
    loaded = synthesis_load_tray(
        task_id=created.task_id,
        pallet_type=pallet_type,
    )

    # 命令7完成后紧接命令3（威纶通“加样流程启动”）；命令3内部负责
    # 取盖/放盖、扫码、天平开关门、称粉/加珠及坩埚返回。
    # unilab:node_uuid=76ec0634-9277-4474-bfa6-b0ea0b4f8969
    dosed = station.dose_recipe(
        task_id=loaded.task_id,
    )
    return {
        "task_id": dosed.task_id,
        "sampling_results": dosed.sampling_results,
    }
