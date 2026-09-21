"""合成工站称粉实验操作。

该模块是一个稳定的业务子合同：设备配置、PLC 地址和协议细节留在
启动图与设备动作中；流程只选择任务参数，并沿 ``ResourceSlot`` 传递
正在处理的坩埚/托盘资源。设备动作成功后资源位置不改变，因此返回原
``ResourceSlot`` 供上层继续追踪其内容状态。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.resources.synthesis_resources import SynthesisCrucible
from yb_sse_devices.synthesis_modbus_station import YBSynthesisModbusStation


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


yb_synthesis_station: YBSynthesisModbusStation = device("yb_synthesis_modbus_station_01")


@workflow(
    workflow_uuid="72cb4f07-9a92-46eb-ae2a-79d08e2c3b67",
    displayname="YB 合成工站称粉",
    description=(
        "PLC 内部完成坩埚扫码绑定后执行称粉；实验操作保持坩埚 ResourceSlot "
        "不变，并把任务参数和设备回执交给上层工作流。"
    ),
    workflow_type="experiment_operation",
)
def synthesis_sampling(
    *,
    resource: Annotated[
        ResourceSlot,
        AllowedResourceTemplates(SynthesisCrucible),
    ],
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
        crucible=resource,
        source_site=source_site,
        task_id=task_id,
        slot_num=slot_num,
        rack_positions=rack_positions,
        masses=masses,
        tolerances=tolerances,
        cubic_type=cubic_type,
        bead_count=bead_count,
        from_outside=from_outside,
        material_names=material_names,
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
