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
def synthesis_powder_bead_rack2(*, task_id: str) -> LegacyTaskResult:
    # unilab:node_uuid=5adb998b-3240-47a8-b19b-63b6694aa76b
    uploaded = station.upload_cubic(task_id=task_id, fetch_cubic_source=1)

    # 旧人工确认节点：d75813b4-62d4-4bbe-8ce8-0ae39ec187ff
    # unilab:node_uuid=76ec0634-9277-4474-bfa6-b0ea0b4f8969 manual_confirmation_timeout_seconds=3600
    started = station.start_recipt(task_id=uploaded.task_id)
    return {"task_id": started.task_id}
