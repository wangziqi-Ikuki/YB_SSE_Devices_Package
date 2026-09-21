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
def synthesis_mixing(*, task_id: str) -> LegacyTaskResult:
    # unilab:node_uuid=78fa5a3b-cd23-4de4-8f40-60b261d9a8e2
    uploaded = station.upload_cubic(task_id=task_id, fetch_cubic_source=0)

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
