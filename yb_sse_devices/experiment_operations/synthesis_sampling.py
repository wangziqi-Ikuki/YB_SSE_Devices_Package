"""合成工站称粉实验操作。

该模块是一个稳定的业务子合同：设备配置、PLC 地址和协议细节留在
启动图与设备动作中；流程只选择任务参数，并沿 ``ResourceSlot`` 传递
正在处理的坩埚/托盘资源。设备动作成功后资源位置不改变，因此返回原
``ResourceSlot`` 供上层继续追踪其内容状态。
"""

from __future__ import annotations

from typing import TypedDict

from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import device, workflow

from yb_sse_devices.synthesis_modbus_station import YBSynthesisModbusStation


class SynthesisSamplingResult(TypedDict):
    resource: ResourceSlot
    task_id: str
    slot_num: int
    accepted: bool


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
    resource: ResourceSlot,
    task_id: str = "",
    slot_num: int = 1,
    rack_positions: list[int] = [1],
    masses: list[float] = [0.9],
    tolerances: list[float] = [0.0007],
    cubic_type: int = 1,
    bead_count: int = 0,
    from_outside: bool = False,
    material_names: list[str] = ["Li2S"],
) -> SynthesisSamplingResult:
    # unilab:node_uuid=4dc40329-4e2d-4e0e-98c0-24c292e20d26
    accepted = yb_synthesis_station.sample(
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
    # 资源仍由 PLC/机械臂夹持在工站流程内，位置没有发生转移；返回同一
    # ResourceSlot 是为了让完整工作流继续持有可追踪资源引用。
    return {
        "resource": resource,
        "task_id": accepted.task_id,
        "slot_num": slot_num,
        "accepted": accepted.accepted,
    }
