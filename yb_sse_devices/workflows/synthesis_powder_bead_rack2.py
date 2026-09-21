"""Converted legacy workflow: powder and bead loading from rack 2."""

from typing import TypedDict

from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)


class LegacyTaskResult(TypedDict):
    task_id: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="2b618ad2-cb04-4ce0-8f0f-4155497740b2",
    displayname="合成工站 加粉加珠流程 料架2",
    description="由旧 JSON 工作流转换；保留料架2上坩埚、人工确认和启动加粉加珠的顺序。",
)
def synthesis_powder_bead_rack2(
    *,
    recipe_name: str = "YB-SIM-Li6PS5Cl",
    formula: str = "Li6PS5Cl",
    synthesis_mass: float = 3.86,
    n_ball_bead: int = 80,
    powder_names: list[str] = ["Li2S", "LiBr", "LiCl", "P2S5"],
    powder_weights: list[float] = [0.9, 0.87, 1.2, 0.88],
    powder_tolerances: list[float] = [0.0007, 0.0005, 0.001, 0.0005],
    powder_pre_adds: list[bool] = [False, True, False, False],
    pallet_type: int = 1,
    cubic_type: int = 1,
    task_slot_nums: list[int] = [1],
    has_bead_bottle: bool = True,
    bead_count: int = 80,
    fetch_cubic_source: int = 1,
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
    )

    # unilab:node_uuid=5adb998b-3240-47a8-b19b-63b6694aa76b
    uploaded = station.upload_cubic(
        task_id=created.task_id,
        fetch_cubic_source=fetch_cubic_source,
    )

    # 旧人工确认节点：d75813b4-62d4-4bbe-8ce8-0ae39ec187ff
    # unilab:node_uuid=76ec0634-9277-4474-bfa6-b0ea0b4f8969 manual_confirmation_timeout_seconds=3600
    started = station.start_recipt(task_id=uploaded.task_id)
    return {"task_id": started.task_id}
