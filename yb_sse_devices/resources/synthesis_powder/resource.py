"""Canonical synthesis powder resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from typing import Any
from ..common import _SynthesisContainer

@resource(
    id="synthesis_powder", displayname="合成粉料盒",
    category=["synthesis", "powder", "container"],
    description="用于合成工站称粉的单批粉料容器；实例条码和批次由启动图提供。",
    metadata={"capacity": None, "capacity_status": "unknown", "dimensions_status": "logical_placeholder"},
)
class SynthesisPowder(_SynthesisContainer):
    resource_id = "synthesis_powder"

__all__ = ["SynthesisPowder"]
