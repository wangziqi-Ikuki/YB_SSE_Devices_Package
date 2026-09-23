from typing import Literal, TypedDict

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from yb_sse_devices.experiment_operations.synthesis_load_tray import synthesis_load_tray
from unilabos.workflow.authoring import device, workflow


class Rack2WeighResult(TypedDict):
    pallet_loaded: bool
    success: bool
    message: str
    qr_codes: list[str]
    weights: list[float]


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="2b618ad2-cb04-4ce0-8f0f-4155497740b2",
    displayname="料架2注粉称重",
    description="启动后先由人工确认托盘和坩埚已放到料架2；确认通过后，OS 向 PLC 写入命令7，把托盘搬到料架1；随后写入命令3，由 PLC 完成扫码、天平开关门、称粉/加珠，并按所选槽位放回坩埚。",
)
def synthesis_powder_bead_rack2(
    *,
    recipe_name: Literal[
        "260727-Li333PS5Cl-R1",
        "260727-Li444PS5Cl-R1",
        "260727-Li1PS5Cl-R1",
        "260727-Li2PS5Cl-R1",
        "260727-Li3PS5Cl-R1",
        "260727-Li4PS5Cl-R1",
        "260901-Li6PS5Cl-R2",
        "260901-Li6PS5Cl-R3",
        "260901-Li5.3P1.0S4.3Cl1.0Br0.7-R1",
        "260901-Li5.5P1.0S4.5Cl0.8Br0.7-R2",
        "260902-Li5.5P1.0S4.5Cl0.8Br0.7-Pre",
        "260902-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-02",
        "260903-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-01",
        "260903-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-02",
        "260903-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-03",
        "260903-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-04",
        "260903-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-05",
        "260903-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-06",
        "260908-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-06",
        "260908-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-05",
        "260908-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-04",
        "260908-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-03",
        "260909-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-01",
        "260909-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-02",
        "260909-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-03",
        "260909-Li5.5P1.0S4.5Cl0.8Br0.7-Pre-04",
        "260909-Li5.5P1.0S4.5Cl0.8Br0.7",
        "20260920-Li5.3P1.0S4.3Cl1.0Br0.7-R1",
        "20260920-Li5.5P1.0S4.5Cl0.8Br0.7-R2",
    ] = "20260920-Li5.3P1.0S4.3Cl1.0Br0.7-R1",
    pallet_type: Literal["4 槽位托盘", "5 槽位托盘", "6 槽位托盘"] = "6 槽位托盘",
    cubic_type: Literal[
        "Al2O3 30*30", "Al2O3 35*40", "Al2O3 40*40", "ZrO2 40*35", "ZrO2 40*46"
    ] = "Al2O3 30*30",
    task_slot_nums: list[int],
    has_bead_bottle: bool = False,
    bead_count: int = 0,
) -> Rack2WeighResult:
    # unilab:node_uuid=4c1a8f6e-0d55-4e67-9f0c-5eb22ab87f7c
    loaded = synthesis_load_tray(
        bead_count=bead_count,
        cubic_type=cubic_type,
        has_bead_bottle=has_bead_bottle,
        pallet_type=pallet_type,
        task_slot_nums=task_slot_nums,
    )
    # unilab:node_uuid=76ec0634-9277-4474-bfa6-b0ea0b4f8969
    dosed = station.dose_selected_slots(
        bead_count=bead_count,
        cubic_type=cubic_type,
        has_bead_bottle=has_bead_bottle,
        pallet_type=pallet_type,
        recipe_name=recipe_name,
        task_slot_nums=task_slot_nums,
    )
    return {
        "pallet_loaded": loaded.success,
        "success": dosed.success,
        "message": dosed.message,
        "qr_codes": dosed.qr_codes,
        "weights": dosed.weights,
    }
