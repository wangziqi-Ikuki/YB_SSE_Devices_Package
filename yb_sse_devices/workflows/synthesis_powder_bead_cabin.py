"""Converted legacy workflow: powder and bead loading from the cabin."""

from typing import TypedDict

from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)


class LegacyTaskResult(TypedDict):
    task_id: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="00cdaee3-7c63-4c77-875d-3d7093d0e5e3",
    displayname="合成工站 加粉加珠流程 方舱",
    description="由旧 JSON 工作流转换；保留方舱上坩埚、两次人工确认和启动加粉加珠的顺序。",
)
def synthesis_powder_bead_cabin(
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
    # unilab:node_uuid=a0cdaee3-7c63-4c77-875d-3d7093d0e5e3
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

    # unilab:node_uuid=b021ed6e-a61d-447e-a321-078e457ec762
    uploaded = station.upload_cubic(
        task_id=created.task_id,
        fetch_cubic_source=fetch_cubic_source,
    )

    # 旧人工确认节点：181c63c6-59fc-4f49-8e25-19cc0a3b8cc4
    # unilab:node_uuid=28cecad0-49fa-4cd0-8b9c-a26f70a9a916 manual_confirmation_timeout_seconds=3600
    closed = station.close_cabin_outer_door(task_id=uploaded.task_id)

    # 旧人工确认节点：bc32d696-e88a-4e68-acce-2b57347178d1
    # unilab:node_uuid=9b3df649-10b0-4ade-88d6-be3221a43cbf manual_confirmation_timeout_seconds=3600
    started = station.start_recipt(task_id=closed.task_id)
    return {"task_id": started.task_id}
