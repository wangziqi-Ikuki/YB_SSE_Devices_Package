"""Canonical synthesis tray rack resource template."""
from __future__ import annotations

from unilabos.registry.decorators import resource
from typing import Any
from ..common import SYNTHESIS_TRAY_RACK_AVAILABLE_SITES, _apply_site_config, _warehouse

@resource(
    id="synthesis_tray_rack", displayname="合成托盘架",
    category=["synthesis", "warehouse", "tray"],
    available_sites=[{'label': 'synthesis_tray_rack_01-0', 'position': {'x': 100.0, 'y': 100.0, 'z': 0.0}, 'size': {'width': 420.0, 'height': 300.0, 'depth': 35.0}, 'content_type': ['synthesis_tray']}, {'label': 'synthesis_tray_rack_01-1', 'position': {'x': 240.0, 'y': 100.0, 'z': 0.0}, 'size': {'width': 420.0, 'height': 300.0, 'depth': 35.0}, 'content_type': ['synthesis_tray']}, {'label': 'synthesis_tray_rack_01-2', 'position': {'x': 380.0, 'y': 100.0, 'z': 0.0}, 'size': {'width': 420.0, 'height': 300.0, 'depth': 35.0}, 'content_type': ['synthesis_tray']}, {'label': 'synthesis_tray_rack_01-3', 'position': {'x': 520.0, 'y': 100.0, 'z': 0.0}, 'size': {'width': 420.0, 'height': 300.0, 'depth': 35.0}, 'content_type': ['synthesis_tray']}],
    description="合成托盘的固定库位架；每个库位只允许 synthesis_tray。",
    metadata={"capacity_status": "fixed_site_count", "physical_dimensions_status": "placeholder"},
)
def synthesis_tray_rack(name: str = "synthesis_tray_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(_warehouse(name, content_type="synthesis_tray", num_items_x=2, num_items_y=2), sites)

__all__ = ["synthesis_tray_rack"]
