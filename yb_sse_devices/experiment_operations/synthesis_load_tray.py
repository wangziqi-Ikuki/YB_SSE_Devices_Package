from typing import Literal, TypedDict

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from unilabos.workflow.authoring import device, workflow


class SynthesisLoadTrayResult(TypedDict):
    success: bool
    message: str
    command: str
    status_code: int
    status_name: str


yb_synthesis_station: YBSynthesisAtomicStation = device(
    "yb_synthesis_atomic_station_01"
)


@workflow(
    workflow_uuid="871ecc8b-9e7c-4e09-8637-3c8c0df42927",
    displayname="YB 合成工站上托盘",
    workflow_type="experiment_operation",
    description="人工确认托盘和坩埚已放在料架2后，由 OS 向 PLC 写入命令7，把托盘搬运到料架1。不调用配方或 TASK 业务接口。",
)
def synthesis_load_tray(
    *,
    pallet_type: Literal["4 槽位托盘", "5 槽位托盘", "6 槽位托盘"] = "6 槽位托盘",
    cubic_type: Literal[
        "Al2O3 30*30", "Al2O3 35*40", "Al2O3 40*40", "ZrO2 40*35", "ZrO2 40*46"
    ] = "Al2O3 30*30",
    task_slot_nums: list[int],
    has_bead_bottle: bool = False,
    bead_count: int = 0,
) -> SynthesisLoadTrayResult:
    # unilab:node_uuid=6ecb8e79-e13e-4c33-84ec-82ebcb9b5a52 manual_confirmation_timeout_seconds=3600
    loaded = yb_synthesis_station.load_pallet_from_rack2(
        bead_count=bead_count,
        cubic_type=cubic_type,
        has_bead_bottle=has_bead_bottle,
        pallet_type=pallet_type,
        task_slot_nums=task_slot_nums,
    )
    return {
        "success": loaded.success,
        "message": loaded.message,
        "command": loaded.command,
        "status_code": loaded.status_code,
        "status_name": loaded.status_name,
    }
