from typing import Literal, TypedDict

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from unilabos.workflow.authoring import device, workflow


class Rack2WeighResult(TypedDict):
    pallet_loaded: bool
    success: bool
    message: str
    qr_codes: list[str]
    weights: list[float]
    resonance_finished: bool
    resonance_unloaded: bool


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="2b618ad2-cb04-4ce0-8f0f-4155497740b2",
    displayname="料架2-加粉加珠声共振流程",
    description="确认托盘和坩埚已放在料架2后，把托盘搬到料架1。4 槽、5 槽、6 槽托盘都按槽位单独选择配方。确认后按槽位加粉、加珠并放回料架1。随后确认声共振参数，放入声共振后先放开工站，等振完再下料。",
)
def synthesis_powder_bead_rack2(
    *,
    slot_recipes: list[Literal[
        "",
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
        "260924-Li5.5P1.0S4.5Cl0.8Br0.7-pre-01",
        "260924-Li5.5P1.0S4.5Cl0.8Br0.7-pre-02",
    ]],
    pallet_type: Literal["4 槽位托盘", "5 槽位托盘", "6 槽位托盘"] = "6 槽位托盘",
    cubic_type: Literal[
        "Al2O3 30*30", "Al2O3 35*40", "Al2O3 40*40", "ZrO2 40*35", "ZrO2 40*46"
    ] = "Al2O3 30*30",
    has_bead_bottle: bool = False,
    bead_count: int = 0,
    resonance_acceleration: int,
    resonance_frequency: int,
    resonance_time: int,
) -> Rack2WeighResult:
    # unilab:node_uuid=4c1a8f6e-0d55-4e67-9f0c-5eb22ab87f7c manual_confirmation_timeout_seconds=3600
    loaded = station.load_pallet_from_rack2(
        bead_count=bead_count,
        cubic_type=cubic_type,
        has_bead_bottle=has_bead_bottle,
        pallet_type=pallet_type,
        slot_recipes=slot_recipes,
    )
    # unilab:node_uuid=76ec0634-9277-4474-bfa6-b0ea0b4f8969 manual_confirmation_timeout_seconds=3600
    dosed = station.dose_selected_slots(
        bead_count=bead_count,
        cubic_type=cubic_type,
        has_bead_bottle=has_bead_bottle,
        pallet_type=pallet_type,
        slot_recipes=slot_recipes,
    )
    # unilab:node_uuid=8f3c1a20-6b14-4d2e-9c55-1a0e7d2b91c4 manual_confirmation_timeout_seconds=3600
    placed = station.run_acoustic_load(
        acceleration=resonance_acceleration,
        duration_minutes=resonance_time,
        frequency=resonance_frequency,
    )
    # unilab:node_uuid=5e8a1c74-2b90-4d6f-a173-9f0c6d4e8a21
    finished = station.wait_acoustic_process()
    # unilab:node_uuid=c2a91e44-5f08-4b77-8d31-6e4b0a9c2f17
    unloaded = station.run_acoustic_unload()
    return {
        "pallet_loaded": loaded.success,
        "success": unloaded.success,
        "message": unloaded.message,
        "qr_codes": dosed.qr_codes,
        "weights": dosed.weights,
        "resonance_finished": finished.success,
        "resonance_unloaded": unloaded.success,
    }
