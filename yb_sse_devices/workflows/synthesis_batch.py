from typing import TypedDict

from yb_sse_devices.resources.synthesis_crucible.resource import SynthesisCrucible
from yb_sse_devices.experiment_operations.synthesis_sampling import synthesis_sampling
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import device, workflow, MaterialCustodyPolicy, MaterialFlowRole, material_source, resource_ref


class SynthesisBatchResult(TypedDict):
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




@workflow(
    workflow_uuid="7e5dabc1-8795-5009-85fe-c9a4c4f0c1e3",
    displayname='YB 合成称粉批次',
    description='选择一个坩埚资源，完成 PLC 扫码绑定和称粉，并保留资源追踪引用。',
)
def synthesis_batch(
    *,
    source_site: str = 'synthesis_crucible_rack_01-0',
    task_id: str = '',
    slot_num: int = 1,
    rack_positions: list[int] = [1],
    masses: list[float] = [0.9],
    tolerances: list[float] = [0.0007],
    cubic_type: int = 1,
    bead_count: int = 0,
    from_outside: bool = False,
    material_names: list[str] = ['Li2S'],
) -> SynthesisBatchResult:
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256b8b
    source_resource = material_source(resource_template=SynthesisCrucible, mode='existing', mount=resource_ref("synthesis_crucible_rack_01"), material_uuid=None, site=source_site, slot_range=None, flow_role=MaterialFlowRole.PRIMARY_SAMPLE, custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE)
    # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b9c
    sampled = synthesis_sampling(bead_count=bead_count, cubic_type=cubic_type, from_outside=from_outside, masses=masses, material_names=material_names, rack_positions=rack_positions, resource=source_resource, slot_num=slot_num, source_site=source_site, task_id=task_id, tolerances=tolerances)
    return {'resource': sampled.resource, 'task_id': sampled.task_id, 'slot_num': slot_num, 'accepted': sampled.accepted, 'success': sampled.success, 'status_name': sampled.status_name, 'qr_code': sampled.qr_code, 'weights': sampled.weights, 'result_codes': sampled.result_codes, 'source_site': sampled.source_site, 'message': sampled.message}
