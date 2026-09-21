"""启动电导工站整批自动实验。"""

from typing import TypedDict

from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.devices.conductivity_station.device import ConductivityStation


class StartConductivityBatchResult(TypedDict):
    result: int
    batch_id: str


conductivity_station: ConductivityStation = device("conductivity_station_01")


@workflow(
    workflow_uuid="3c8f1a62-9b74-4e2d-a5c1-7d0e8f4b2196",
    displayname="电导工站启动整批",
    description="备料完成后调用 start_batch，工站连续执行 1–13 步并返回工站响应。",
)
def start_conductivity_batch() -> StartConductivityBatchResult:
    # unilab:node_uuid=9d2e6c11-4a83-4f70-b1c5-0e8a37d5f4a2
    started = conductivity_station.start_batch()
    return {
        "result": started.result,
        "batch_id": started.batch_id,
    }
