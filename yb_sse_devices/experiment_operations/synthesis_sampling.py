from typing import Annotated, TypedDict

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from yb_sse_devices.resources.synthesis_crucible.resource import SynthesisCrucible
from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import device, workflow


class SynthesisSamplingResult(TypedDict):
    resource: ResourceSlot
    task_id: str
    slot_num: int
    accepted: bool
    success: bool
    status_name: str
    qr_code: str
    weights: list[float]
    result_codes: list[int]
    source_site: str
    message: str


yb_synthesis_station: YBSynthesisAtomicStation = device(
    "yb_synthesis_atomic_station_01"
)


@workflow(
    workflow_uuid="72cb4f07-9a92-46eb-ae2a-79d08e2c3b67",
    displayname="YB 合成工站称粉",
    workflow_type="experiment_operation",
    description="PLC 内部完成坩埚扫码绑定后执行称粉；实验操作保持坩埚 ResourceSlot 不变，并把任务参数和设备回执交给上层工作流。",
)
def synthesis_sampling(
    *,
    resource: Annotated[ResourceSlot, AllowedResourceTemplates(SynthesisCrucible)],
    source_site: str = "synthesis_crucible_rack_01-0",
    task_id: str = "",
    slot_num: int = 1,
    rack_positions: list[int],
    masses: list[float],
    tolerances: list[float],
    cubic_type: int = 1,
    bead_count: int = 0,
    from_outside: bool = False,
    material_names: list[str],
) -> SynthesisSamplingResult:
    # unilab:node_uuid=4dc40329-4e2d-4e0e-98c0-24c292e20d26
    accepted = yb_synthesis_station.sample_with_materials(
        bead_count=bead_count,
        crucible=resource,
        cubic_type=cubic_type,
        from_outside=from_outside,
        masses=masses,
        material_names=material_names,
        rack_positions=rack_positions,
        slot_num=slot_num,
        source_site=source_site,
        task_id=task_id,
        tolerances=tolerances,
    )
    return {
        "resource": resource,
        "task_id": accepted.task_id,
        "slot_num": slot_num,
        "accepted": accepted.accepted,
        "success": accepted.success,
        "status_name": accepted.status_name,
        "qr_code": accepted.qr_code,
        "weights": accepted.weights,
        "result_codes": accepted.result_codes,
        "source_site": accepted.source_site,
        "message": accepted.message,
    }
