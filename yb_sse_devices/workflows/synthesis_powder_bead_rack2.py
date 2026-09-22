"""Converted legacy workflow: powder and bead loading from rack 2."""

from typing import TypedDict

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
    recipe_name: str = "YB-LiCl-P2S5-2G",
    formula: str = "LiCl-P2S5",
    synthesis_mass: float = 4.0,
    n_ball_bead: int = 0,
    # Lists are workflow inputs.  ``None`` keeps the contract optional and
    # avoids sharing mutable defaults between OS invocations.
    powder_names: list[str] | None = None,
    powder_weights: list[float] | None = None,
    powder_tolerances: list[float] | None = None,
    powder_pre_adds: list[bool] | None = None,
    pallet_type: int = 3,
    cubic_type: int = 1,
    task_slot_nums: list[int] | None = None,
    has_bead_bottle: bool = False,
    bead_count: int = 0,
    fetch_cubic_source: int = 1,
    # PLC command 7 has six tray-slot type fields.  These are explicit PLC
    # payload values, separate from the OS TASK slot numbers above.
    pallet_slot_types: list[int] | None = None,
    # None derives the PLC wire source from fetch_cubic_source when a bead
    # bottle is present; pass an integer only for a confirmed special setup.
    bead_source: int | None = None,
    # PLC command 7 destination (40003).  ``1`` is the rack-1 destination
    # used by the current station, while remaining an editable input.
    crucible_destination: int = 1,
    # Physical powder-rack positions are deliberately required for a real
    # run.  The PLC project/HANDOFF does not confirm their numbering yet.
    powder_rack_positions: list[int] | None = None,
    # 命令3会在天平称重完成后开门取回坩埚，再用此字段写入“放球磨罐位置”。
    # 真实 PLC 必须在确认现场位置映射后显式传入；None 会让直接模式拒绝动作。
    crucible_return_positions: list[int] | None = None,
    crucible_return_location: str = "synthesis_crucible_rack_01",
    # Optional actual-weight confirmation payload.  CMD3 still performs the
    # physical weighing; this JSON is only the OS-side confirmation record.
    real_weights_json: str = "",
) -> LegacyTaskResult:
    # unilab:node_uuid=ab618ad2-cb04-4ce0-8f0f-4155497740b2
    created = station.create_batch(
        recipe_name=recipe_name,
        formula=formula,
        synthesis_mass=synthesis_mass,
        n_ball_bead=n_ball_bead,
        powder_names=powder_names,
        powder_weights=powder_weights,
        powder_tolerances=powder_tolerances,
        powder_pre_adds=powder_pre_adds,
        pallet_type=pallet_type,
        cubic_type=cubic_type,
        task_slot_nums=task_slot_nums,
        has_bead_bottle=has_bead_bottle,
        bead_count=bead_count,
        fetch_cubic_source=fetch_cubic_source,
        powder_rack_positions=powder_rack_positions,
        pallet_slot_types=pallet_slot_types,
    )

    # 上托盘实验操作内部带人工确认：操作员先将托盘和坩埚放到料架2，
    # 确认通过后才启动 PLC 命令7（威纶通“上托盘流程启动”）。
    # unilab:node_uuid=4c1a8f6e-0d55-4e67-9f0c-5eb22ab87f7c
    loaded = synthesis_load_tray(
        task_id=created.task_id,
        fetch_cubic_source=fetch_cubic_source,
        destination=crucible_destination,
        pallet_type=pallet_type,
        pallet_slot_types=pallet_slot_types,
        bead_source=bead_source,
    )

    # 命令7完成后紧接命令3（威纶通“加样流程启动”）；命令3内部负责
    # 取盖/放盖、扫码、天平开关门、称粉/加珠及坩埚返回。
    # unilab:node_uuid=76ec0634-9277-4474-bfa6-b0ea0b4f8969
    dosed = station.dose_recipe(
        task_id=loaded.task_id,
        real_weights_json=real_weights_json,
        powder_rack_positions=powder_rack_positions,
        crucible_return_positions=crucible_return_positions,
        crucible_return_location=crucible_return_location,
    )
    return {
        "task_id": dosed.task_id,
        "sampling_results": dosed.sampling_results,
    }
