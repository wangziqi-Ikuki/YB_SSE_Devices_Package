"""Canonical synthesis crucible resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from typing import Any
from ..common import _SynthesisContainer

@resource(
    id="synthesis_crucible", displayname="合成坩埚",
    category=["synthesis", "crucible", "container"],
    description="由机械臂夹取并在 PLC 内部扫码绑定的合成坩埚。",
    metadata={"capacity": None, "capacity_status": "unknown", "dimensions_status": "logical_placeholder"},
)
class SynthesisCrucible(_SynthesisContainer):
    resource_id = "synthesis_crucible"
    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 90.0)
        kwargs.setdefault("size_y", 90.0)
        kwargs.setdefault("size_z", 70.0)
        super().__init__(name=name, **kwargs)

__all__ = ["SynthesisCrucible"]
