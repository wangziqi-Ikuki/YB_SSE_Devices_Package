"""Converted legacy synthesis mixing workflow."""

from typing import TypedDict

from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)


class LegacyTaskResult(TypedDict):
    task_id: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="bbf54bc0-4119-48be-a7b7-29d84a6a45a9",
    displayname="合成工站混料流程",
    description="由旧 JSON 工作流转换；保留方舱上料、两次人工确认、加粉加珠及声共振三步动作。",
)
def synthesis_mixing(
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
    fetch_cubic_source: int = 0,
) -> LegacyTaskResult:
    # unilab:node_uuid=abf54bc0-4119-48be-a7b7-29d84a6a45a9
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

    # unilab:node_uuid=78fa5a3b-cd23-4de4-8f40-60b261d9a8e2
    uploaded = station.upload_cubic(
        task_id=created.task_id,
        fetch_cubic_source=fetch_cubic_source,
    )

    # 旧人工确认节点：3d2cd0b2-f7e8-436b-9fb2-0e7b7ae9158f
    # unilab:node_uuid=96e31dcd-0658-4313-8cd5-f258a26ae8b8 manual_confirmation_timeout_seconds=3600
    closed = station.close_cabin_outer_door(task_id=uploaded.task_id)

    # 旧人工确认节点：64fa1746-304f-4823-8601-224c9e38a46f
    # unilab:node_uuid=67d992df-d7f3-4a8e-9025-a5bbde1ea25a manual_confirmation_timeout_seconds=3600
    started = station.start_recipt(task_id=closed.task_id)

    # unilab:node_uuid=6847ac6f-3dc8-4eae-8b63-067e55f195ab
    resonating = station.start_acoustic_resonance(task_id=started.task_id)
    # unilab:node_uuid=6abb10b7-5bce-465c-a0d7-737bcb8e7f22
    fetched = station.fetch_acoustic_resonance(task_id=resonating.task_id)
    # unilab:node_uuid=9ea1db00-42bd-474c-b554-d2679bb688a5
    finished = station.finish_acoustic_resonance(task_id=fetched.task_id)
    return {"task_id": finished.task_id}
