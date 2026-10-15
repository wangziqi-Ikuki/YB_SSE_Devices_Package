"""Independent lock identity for the acoustic resonator.

The station moves the tray. This device only occupies the chamber and
polls whether vibration has finished, so the arm can be used elsewhere.
"""

from __future__ import annotations

from typing import Any, TypedDict

from unilabos.registry.decorators import action, device

from yb_sse_devices.devices.yb_synthesis_modbus_station.device import (
    YBSynthesisModbusStation,
)


class ChamberWaitResult(TypedDict):
    accepted: bool
    success: bool
    message: str
    command: str
    status_code: int
    status_name: str


@device(
    id="yb_acoustic_chamber",
    category=["synthesis", "process"],
    display_name="YB acoustic chamber",
    description="Occupies the resonator while vibration runs; does not move the arm.",
    version="0.1.0",
)
class YBAcousticChamber:
    """Lock identity for the resonator while vibration is running."""

    def __init__(
        self,
        device_id: str | None = None,
        config: dict[str, Any] | None = None,
        **runtime_config: Any,
    ) -> None:
        resolved = dict(config or {})
        for key, value in runtime_config.items():
            resolved.setdefault(key, value)
        resolved.setdefault("simulation", True)
        self.device_id = device_id or "yb_acoustic_chamber"
        self.station = YBSynthesisModbusStation(
            device_id=f"{self.device_id}_plc", config=resolved
        )

    @action(
        displayname="Wait for acoustic process",
        description="Hold the chamber and poll completion. Do not lock the arm.",
    )
    def wait_acoustic_process(self, timeout: float = 14400.0) -> ChamberWaitResult:
        """Poll vibration status while holding only the chamber identity."""

        return self.station.wait_acoustic_process(timeout=timeout)


__all__ = ["YBAcousticChamber"]
