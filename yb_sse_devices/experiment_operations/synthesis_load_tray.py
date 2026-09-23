"""YB 合成工站的上托盘可复用实验操作。"""

# Keep the operation source revisioned with the package-owned catalog contract.

from typing import Literal, TypedDict

from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)


class SynthesisLoadTrayResult(TypedDict):
    task_id: str
    state: str
    message: str


yb_synthesis_station: YBSynthesisAtomicStation = device(
    "yb_synthesis_atomic_station_01"
)


@workflow(
    workflow_uuid="871ecc8b-9e7c-4e09-8637-3c8c0df42927",
    displayname="YB 合成工站上托盘",
    workflow_type="experiment_operation",
    description=(
        "人工确认托盘和坩埚已放在料架2后，执行 PLC 命令7将托盘和坩埚"
        "搬运到料架1；实验操作只负责上托盘，不执行加样。"
    ),
)
def synthesis_load_tray(
    *,
    task_id: str,
    pallet_type: Literal["4 槽位托盘", "5 槽位托盘", "6 槽位托盘"] = "6 槽位托盘",
) -> SynthesisLoadTrayResult:
    # 人工确认：操作员先将托盘和坩埚放到料架2，并核对托盘槽位与坩埚类型。
    # unilab:node_uuid=6ecb8e79-e13e-4c33-84ec-82ebcb9b5a52 manual_confirmation_timeout_seconds=3600
    loaded = yb_synthesis_station.load_big_crucible(
        task_id=task_id,
        # This experiment operation is the rack-2 route.  Source and PLC
        # destination are fixed station wiring, not operator inputs.
        fetch_cubic_source=1,
        destination=1,
        pallet_type=pallet_type,
    )
    return {
        "task_id": loaded.task_id,
        "state": loaded.state,
        "message": loaded.message,
    }
