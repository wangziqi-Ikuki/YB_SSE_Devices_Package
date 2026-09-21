"""Canonical synthesis tray resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from typing import Any
from ..common import _SynthesisContainer

@resource(
    id="synthesis_tray", displayname="合成托盘",
    category=["synthesis", "tray", "holder"],
    description="装载坩埚和粉料瓶、供机械臂搬运的合成托盘。",
    metadata={"capacity": None, "capacity_status": "unknown", "dimensions_status": "logical_placeholder"},
)
class SynthesisTray(_SynthesisContainer):
    resource_id = "synthesis_tray"
    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 420.0)
        kwargs.setdefault("size_y", 300.0)
        kwargs.setdefault("size_z", 35.0)
        super().__init__(name=name, **kwargs)

__all__ = ["SynthesisTray"]
