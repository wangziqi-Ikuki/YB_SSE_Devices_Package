"""YB 合成工站单批次原子动作工作流。"""

from typing import TypedDict

from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    device,
    material_source,
    resource_ref,
    resources,
    workflow,
)

from yb_sse_devices.resources.synthesis_bead_bottle.resource import SynthesisBeadBottle
from yb_sse_devices.resources.synthesis_crucible.resource import SynthesisCrucible
from yb_sse_devices.resources.synthesis_powder.resource import SynthesisPowder
from yb_sse_devices.devices.yb_synthesis_atomic_station.device import YBSynthesisAtomicStation


class Result(TypedDict):
    task_id: str
    post_id: str
    qrcode: str
    lot_id: str
    small_cubics: list[str]
    ledger_json: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="f7c79ca5-1ed4-4d2a-9571-a8bbf9786c44",
    displayname="YB 合成批次（原子动作）",
    description="按 SZLab 风格把合成工站拆成可追踪的业务原子动作；PLC CMD_SAMPLE 内部扫码、称粉和加珠仍保持一个连续动作。",
)
def synthesis_atomic_single(
    *,
    recipe_name: str = "YB-SIM-Li6PS5Cl",
    formula: str = "Li6PS5Cl",
    synthesis_mass: float = 3.86,
    n_ball_bead: int = 80,
    powder_names: list[str] = ["Li2S", "LiBr", "LiCl", "P2S5"],
    powder_weights: list[float] = [0.9, 0.87, 1.2, 0.88],
    powder_tolerances: list[float] = [0.0007, 0.0005, 0.001, 0.0005],
    powder_pre_adds: list[bool] = [False, True, False, False],
    pallet_type: int = 1,
    cubic_type: int = 1,
    task_slot_nums: list[int] = [1],
    has_bead_bottle: bool = True,
    bead_count: int = 80,
    fetch_cubic_source: int = 1,
    firing_type: int = 0,
) -> Result:
    # 这些 source 节点是启动图里的实际库存候选；动作参数继续传递
    # ResourceSlot，OS 才能同时画出物料输入和原子动作链。
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c90
    source_crucible = material_source(
        resource_template=SynthesisCrucible,
        mode="existing",
        mount=resource_ref("synthesis_crucible_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c97
    source_powder_1 = material_source(
        resource_template=SynthesisPowder,
        mode="existing",
        mount=resource_ref("synthesis_powder_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.REAGENT,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c98
    source_powder_2 = material_source(
        resource_template=SynthesisPowder,
        mode="existing",
        mount=resource_ref("synthesis_powder_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.REAGENT,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c99
    source_powder_3 = material_source(
        resource_template=SynthesisPowder,
        mode="existing",
        mount=resource_ref("synthesis_powder_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.REAGENT,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c9a
    source_powder_4 = material_source(
        resource_template=SynthesisPowder,
        mode="existing",
        mount=resource_ref("synthesis_powder_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.REAGENT,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c9b
    source_bead_bottle = material_source(
        resource_template=SynthesisBeadBottle,
        mode="existing",
        mount=resource_ref("synthesis_bead_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.REAGENT,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )
    # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c9c
    source_small_crucible = material_source(
        resource_template=SynthesisCrucible,
        mode="existing",
        mount=resource_ref("synthesis_crucible_rack_01"),
        material_uuid=None,
        site=None,
        slot_range=None,
        flow_role=MaterialFlowRole.CONSUMABLE,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )

    with resources("yb_synthesis_atomic_station_01"):
        # 批次初始化与大坩埚上料
        # unilab:node_uuid=8d62b9fd-85b6-4c6c-8f6d-8c4f61256c91
        batch = station.create_batch(
            recipe_name=recipe_name,
            formula=formula,
            synthesis_mass=synthesis_mass,
            n_ball_bead=n_ball_bead,
            powder_names=powder_names,
            powder_weights=powder_weights,
            powder_tolerances=powder_tolerances,
            powder_pre_adds=powder_pre_adds,
            pallet_type=pallet_type,
            cubic_type=cubic_type,
            task_slot_nums=task_slot_nums,
            has_bead_bottle=has_bead_bottle,
            bead_count=bead_count,
            fetch_cubic_source=fetch_cubic_source,
        )
        # unilab:node_uuid=f0eeb0d9-7e4e-43a4-a3a9-f8d9adac2b91
        loaded = station.load_big_crucible(
            task_id=batch.task_id,
            fetch_cubic_source=fetch_cubic_source,
            crucible=source_crucible,
        )

        # 配方确认与内部扫码称粉加珠
        # unilab:node_uuid=c3d8bbb9-dd03-4f48-853d-71c52163c574
        confirmed = station.confirm_recipe(task_id=loaded.task_id)
        # unilab:node_uuid=ca6fae36-a382-4e6b-8e12-82ab162484e9
        dosed = station.dose_recipe(
            task_id=confirmed.task_id,
            crucible=loaded.crucible,
            powder_1=source_powder_1,
            powder_2=source_powder_2,
            powder_3=source_powder_3,
            powder_4=source_powder_4,
            bead_bottle=source_bead_bottle,
        )

        # 声共振上料与下料
        # unilab:node_uuid=3b3495b6-5323-4125-8cbd-2abb65aef15a
        resonance_started = station.start_resonance(
            task_id=dosed.task_id,
            crucible=dosed.crucible,
        )
        # unilab:node_uuid=4a0fd343-d210-4088-b988-de9582d7eb2d
        resonated = station.unload_resonance(
            task_id=resonance_started.task_id,
            crucible=resonance_started.crucible,
        )

        # 扫码装瓶与 POST/LOT 后处理
        # unilab:node_uuid=77bb3dd1-bc1c-46dc-b0dc-aff84985238b
        bottle_scanned = station.scan_bottle(
            task_id=resonated.task_id,
            crucible=resonated.crucible,
        )
        # unilab:node_uuid=285237e3-28c1-42fb-aeae-db6c87d27731
        bottled = station.prepare_post(
            task_id=bottle_scanned.task_id,
            qrcode=bottle_scanned.qrcode,
            crucible=bottle_scanned.crucible,
        )

        # 小坩埚分配与烧结计划
        # unilab:node_uuid=96bbcc66-f109-4e25-846c-acd625e62c3e
        assigned = station.assign_small_crucible(
            post_id=bottled.post_id,
            lot_id=bottled.lot_id,
            firing_type=firing_type,
            small_crucible=source_small_crucible,
        )
        # unilab:node_uuid=81d6be4d-74be-483e-ab07-e5993ea12623
        scheduled = station.confirm_firing_schedule(
            post_id=bottled.post_id,
            lot_id=assigned.lot_id,
            firing_type=firing_type,
            small_crucible=assigned.small_crucible,
        )

        # 烧结与出炉
        # unilab:node_uuid=ae72dd25-45e3-44c3-b0d2-60b200dfb81a
        sintering = station.start_sintering(
            post_id=bottled.post_id,
            small_crucible=scheduled.small_crucible,
        )
        # unilab:node_uuid=51d51b9a-3d38-436b-bfd5-7931d36b45a3
        unloaded = station.unload_sintered(
            post_id=bottled.post_id,
            firing_type=firing_type,
            small_crucible=sintering.small_crucible,
        )
    return {
        "task_id": unloaded.task_id,
        "post_id": unloaded.post_id,
        "qrcode": bottled.qrcode,
        "lot_id": bottled.lot_id,
        "small_cubics": unloaded.small_cubics,
        "ledger_json": unloaded.ledger_json,
    }
