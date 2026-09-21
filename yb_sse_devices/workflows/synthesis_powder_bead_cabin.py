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
def synthesis_powder_bead_cabin(*, task_id: str) -> LegacyTaskResult:
    # unilab:node_uuid=b021ed6e-a61d-447e-a321-078e457ec762
    uploaded = station.upload_cubic(task_id=task_id, fetch_cubic_source=0)

    # 旧人工确认节点：181c63c6-59fc-4f49-8e25-19cc0a3b8cc4
    # unilab:node_uuid=28cecad0-49fa-4cd0-8b9c-a26f70a9a916 manual_confirmation_timeout_seconds=3600
    closed = station.close_cabin_outer_door(task_id=uploaded.task_id)

    # 旧人工确认节点：bc32d696-e88a-4e68-acce-2b57347178d1
    # unilab:node_uuid=9b3df649-10b0-4ade-88d6-be3221a43cbf manual_confirmation_timeout_seconds=3600
    started = station.start_recipt(task_id=closed.task_id)
    return {"task_id": started.task_id}
