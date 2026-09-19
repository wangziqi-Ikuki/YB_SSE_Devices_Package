"""YB 合成工站单批次原子动作工作流。"""

from typing import TypedDict

from unilabos.workflow.authoring import device, resources, workflow

from yb_sse_devices.synthesis_atomic import YBSynthesisAtomicStation


class Result(TypedDict):
    task_id: str
    post_id: str
    qrcode: str
    lot_id: str
    small_cubics: list[str]
    ledger_json: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="f7c79ca5-1ed4-4d2a-9571-a8bbf9786c45",
    displayname="YB 合成原子动作：单批次仿真",
    description="OS 逐动作执行取坩埚、内部扫码称粉、声共振、装瓶、分配小坩埚、烧结和出炉。",
)
def synthesis_atomic_single() -> Result:
    with resources("yb_synthesis_atomic_station_01"):
        # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c91
        batch = station.create_batch(task_slot_nums=[1])
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b91
        loaded = station.load_big_crucible(task_id=batch.task_id)
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b92
        dosed = station.dose_recipe(task_id=loaded.task_id)
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b93
        resonated = station.resonate_and_unload(task_id=dosed.task_id)
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b94
        bottled = station.bottle_and_prepare_post(task_id=resonated.task_id)
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b95
        assigned = station.assign_small_crucibles(
            post_id=bottled.post_id, lot_id=bottled.lot_id
        )
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b96
        unloaded = station.sinter_and_unload(post_id=assigned.post_id)
    return {
        "task_id": unloaded.task_id,
        "post_id": unloaded.post_id,
        "qrcode": bottled.qrcode,
        "lot_id": bottled.lot_id,
        "small_cubics": unloaded.small_cubics,
        "ledger_json": unloaded.ledger_json,
    }

