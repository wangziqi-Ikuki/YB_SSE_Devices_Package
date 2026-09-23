from typing import Literal, TypedDict

from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from unilabos.workflow.authoring import device, workflow


class JouleHeatingResult(TypedDict):
    heating_finished: bool
    success: bool
    message: str


station: YBSynthesisAtomicStation = device("yb_synthesis_atomic_station_01")


@workflow(
    workflow_uuid="8c4e1b70-6a2d-4f58-9c31-7d0a6e5b91f2",
    displayname="焦耳热加热",
    description="确认声共振已下料且小坩埚已人工加料后，按勾选的待机位把小坩埚放入焦耳热并加热。石墨板由人工放入，只选择载体。加热完成后，操作员等待一分钟再确认，小坩埚放回原来的待机位。",
)
def joule_heating(
    *,
    carrier_type: Literal["椭圆石墨舟", "圆形石墨舟", "石墨板"] = "椭圆石墨舟",
    heating_mode: Literal["恒温", "斜率多段"] = "恒温",
    pickup_positions: list[int] | None = None,
    constant_temperature: int = 0,
    constant_hold_minutes: int = 0,
    slope_segments: str = "[]",
) -> JouleHeatingResult:
    # unilab:node_uuid=a1f0c2d4-7b38-4e61-9a05-2c8d6f1e4b70 manual_confirmation_timeout_seconds=3600
    heated = station.run_joule_heating(
        carrier_type=carrier_type,
        constant_hold_minutes=constant_hold_minutes,
        constant_temperature=constant_temperature,
        heating_mode=heating_mode,
        pickup_positions=pickup_positions,
        slope_segments=slope_segments,
    )
    # unilab:node_uuid=b7e2a9c1-5d46-4f83-8b20-6a1c3e9d0f54 manual_confirmation_timeout_seconds=3600
    unloaded = station.unload_joule_heating(
        carrier_type=carrier_type,
        pickup_positions=pickup_positions,
    )
    return {
        "heating_finished": heated.success,
        "success": unloaded.success,
        "message": unloaded.message,
    }
