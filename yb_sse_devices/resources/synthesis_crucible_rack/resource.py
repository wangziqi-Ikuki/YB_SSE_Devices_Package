"""Canonical synthesis crucible rack resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from typing import Any
from ..common import SYNTHESIS_CRUCIBLE_RACK_AVAILABLE_SITES, _apply_site_config, _warehouse

@resource(
    id="synthesis_crucible_rack", displayname="合成坩埚架",
    category=["synthesis", "warehouse", "crucible"],
    available_sites=[{'label': 'synthesis_crucible_rack_01-0', 'position': {'x': 80.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-1', 'position': {'x': 160.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-2', 'position': {'x': 240.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-3', 'position': {'x': 320.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-4', 'position': {'x': 400.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-5', 'position': {'x': 480.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-6', 'position': {'x': 560.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-7', 'position': {'x': 640.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-8', 'position': {'x': 720.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}, {'label': 'synthesis_crucible_rack_01-9', 'position': {'x': 800.0, 'y': 80.0, 'z': 0.0}, 'size': {'width': 90.0, 'height': 90.0, 'depth': 70.0}, 'content_type': ['synthesis_crucible']}],
    description="合成坩埚的固定 2×5 库位架；每个库位只允许 synthesis_crucible。",
    metadata={"capacity_status": "fixed_site_count", "physical_dimensions_status": "placeholder"},
)
def synthesis_crucible_rack(name: str = "synthesis_crucible_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(_warehouse(name, content_type="synthesis_crucible"), sites)

__all__ = ["synthesis_crucible_rack"]
