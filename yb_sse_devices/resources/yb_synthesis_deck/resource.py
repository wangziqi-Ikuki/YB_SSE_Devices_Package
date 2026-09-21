"""Canonical YB synthesis resource deck."""

from __future__ import annotations

from unilabos.registry.decorators import resource

from typing import Any

from ..common import Coordinate, Deck
from ..synthesis_bead_rack.resource import synthesis_bead_rack
from ..synthesis_crucible_rack.resource import synthesis_crucible_rack
from ..synthesis_powder_rack.resource import synthesis_powder_rack
from ..synthesis_tray_rack.resource import synthesis_tray_rack


@resource(
    id="yb_synthesis_deck",
    displayname="YB 合成工站台面",
    category=["synthesis", "deck"],
    description="合成工站资源根节点，包含粉料、坩埚、磨球瓶和托盘料架。",
    icon="synthesis_station.webp",
)
class YBSynthesisDeck(Deck):
    def __init__(
        self,
        name: str = "yb_synthesis_deck",
        size_x: float = 1200.0,
        size_y: float = 900.0,
        size_z: float = 500.0,
        setup: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            name=name,
            category="deck",
            **kwargs,
        )
        self.warehouses: dict[str, Any] = {}
        self.warehouse_locations: dict[str, Any] = {}
        if setup:
            self.setup()

    def setup(self) -> None:
        if self.warehouses:
            return
        self.warehouses = {
            "powder_rack": synthesis_powder_rack("synthesis_powder_rack"),
            "crucible_rack": synthesis_crucible_rack("synthesis_crucible_rack"),
            "bead_rack": synthesis_bead_rack("synthesis_bead_rack"),
            "tray_rack": synthesis_tray_rack("synthesis_tray_rack"),
        }
        for index, rack in enumerate(self.warehouses.values()):
            location = Coordinate(x=50.0, y=80.0 + index * 200.0, z=0.0)
            self.warehouse_locations[getattr(rack, "name", str(index))] = location
            self.assign_child_resource(rack, location=location)


__all__ = ["YBSynthesisDeck"]
