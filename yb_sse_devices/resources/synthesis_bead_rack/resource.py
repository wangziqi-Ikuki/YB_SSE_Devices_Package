"""Canonical synthesis bead bottle rack resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from typing import Any
from ..common import SYNTHESIS_BEAD_RACK_AVAILABLE_SITES, _apply_site_config, _warehouse

@resource(
    id="synthesis_bead_rack", displayname="合成磨球瓶架",
    category=["synthesis", "warehouse", "bead"],
    available_sites=[{'label': 'synthesis_bead_rack_01-0', 'position': {'x': 80.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 70.0, 'height': 70.0, 'depth': 50.0}, 'content_type': ['synthesis_bead_bottle']}, {'label': 'synthesis_bead_rack_01-1', 'position': {'x': 160.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 70.0, 'height': 70.0, 'depth': 50.0}, 'content_type': ['synthesis_bead_bottle']}, {'label': 'synthesis_bead_rack_01-2', 'position': {'x': 240.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 70.0, 'height': 70.0, 'depth': 50.0}, 'content_type': ['synthesis_bead_bottle']}, {'label': 'synthesis_bead_rack_01-3', 'position': {'x': 320.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 70.0, 'height': 70.0, 'depth': 50.0}, 'content_type': ['synthesis_bead_bottle']}],
    description="合成磨球瓶的固定库位架；每个库位只允许 synthesis_bead_bottle。",
    metadata={"capacity_status": "fixed_site_count", "physical_dimensions_status": "placeholder"},
)
def synthesis_bead_rack(name: str = "synthesis_bead_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(_warehouse(name, content_type="synthesis_bead_bottle", num_items_x=2, num_items_y=2), sites)

__all__ = ["synthesis_bead_rack"]
