"""YB 合成工站最小完整工作流。

该流程只负责把上游选择的坩埚资源交给已发布的称粉实验操作；配方、
PLC 地址和扫描器行为由设备包配置及 PLC 程序决定。后续接入配方上传、
烧结和声学检测时，可在此工作流中继续沿用相同的 ResourceSlot 链。
"""

from __future__ import annotations

from typing import TypedDict

from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    material_source,
    resource_ref,
    workflow,
)

from yb_sse_devices.experiment_operations.synthesis_sampling import (
    SynthesisSamplingResult,
    synthesis_sampling,
)
from yb_sse_devices.resources.synthesis_resources import SynthesisCrucible


class SynthesisBatchResult(TypedDict):
    resource: ResourceSlot
    task_id: str
    slot_num: int
    accepted: bool


@workflow(
    workflow_uuid="f7c79ca5-1ed4-4d2a-9571-a8bbf9786c44",
    displayname="YB 合成称粉批次",
    description="选择一个坩埚资源，完成 PLC 扫码绑定和称粉，并保留资源追踪引用。",
)
def synthesis_batch(
    *,
    source_site: str = "synthesis_crucible_rack_01-0",
    task_id: str = "",
    slot_num: int = 1,
    rack_positions: list[int] = [1],
    masses: list[float] = [0.9],
    tolerances: list[float] = [0.0007],
    cubic_type: int = 1,
    bead_count: int = 0,
    from_outside: bool = False,
    material_names: list[str] = ["Li2S"],
) -> SynthesisBatchResult:
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256b8b
    source_resource = material_source(
        resource_template=SynthesisCrucible,
        mode="existing",
        mount=resource_ref("synthesis_crucible_rack_01"),
        material_uuid=None,
        site=source_site,
        slot_range=None,
        flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )

    # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b9c
    sampled = synthesis_sampling(
        resource=source_resource,
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
        "resource": sampled.resource,
        "task_id": sampled.task_id,
        "slot_num": slot_num,
        "accepted": sampled.accepted,
    }
