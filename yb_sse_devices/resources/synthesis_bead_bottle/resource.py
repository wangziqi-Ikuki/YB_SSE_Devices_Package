"""Canonical synthesis bead bottle resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from ..common import _SynthesisContainer

@resource(
    id="synthesis_bead_bottle", displayname="合成磨球瓶",
    category=["synthesis", "bead", "container"],
    description="合成工艺加珠步骤使用的磨球瓶。",
    metadata={"capacity": None, "capacity_status": "unknown", "dimensions_status": "logical_placeholder"},
)
class SynthesisBeadBottle(_SynthesisContainer):
    resource_id = "synthesis_bead_bottle"

__all__ = ["SynthesisBeadBottle"]
