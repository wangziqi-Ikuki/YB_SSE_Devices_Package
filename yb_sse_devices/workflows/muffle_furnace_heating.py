from typing import Literal, TypedDict

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from unilabos.workflow.authoring import device, workflow


class MuffleFurnaceResult(TypedDict):
    loaded: bool
    heating_finished: bool
    success: bool
    message: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="c4a8e2d1-7f63-4b90-9c15-2e6d8a4b71f0",
    displayname="马弗炉加热",
    description="先人工确认小坩埚已放入石英坩埚，并放在料架2的对应槽位。确认后机械臂送入同号马弗炉，放入后即放开工站。烧结期间只等待该炉完成，机械臂可以去做别的任务。加热完成后再把石英坩埚放回原来的槽位。槽位1到4对应马弗炉1到4。",
)
def muffle_furnace_heating(
    *,
    quartz_slot: Literal[1, 2, 3, 4] = 1,
    furnace_segments: str = "[]",
) -> MuffleFurnaceResult:
    # unilab:node_uuid=d1e4a8c2-6b70-4f91-8a33-1c5e9d2b70a4 manual_confirmation_timeout_seconds=3600
    loaded = station.run_muffle_load(
        furnace_segments=furnace_segments,
        quartz_slot=quartz_slot,
    )
    # unilab:node_uuid=e7b2c9d4-1a58-4e60-9f12-8d3c6a5b40e1
    finished = station.wait_muffle_process(quartz_slot=quartz_slot)
    # unilab:node_uuid=f3a6d1e8-2c49-4b77-8e05-9a1d4c7e60b2
    unloaded = station.run_muffle_unload(quartz_slot=quartz_slot)
    return {
        "loaded": loaded.success,
        "heating_finished": finished.success,
        "success": unloaded.success,
        "message": unloaded.message,
    }
