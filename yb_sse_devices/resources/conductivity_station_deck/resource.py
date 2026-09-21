"""Canonical conductivity station deck and its three fixed racks."""

from __future__ import annotations

from unilabos.registry.decorators import resource

from typing import Any

from ..common import Coordinate, Deck, conductivity_rack_layer
from ...protocol import LAYER_WAREHOUSE_NAMES, RACK_LAYERS, SLOT_LABELS


LAYER_Y_PITCH = 240.0


@resource(
    id="conductivity_station_deck",
    displayname="电导率测试工站台面",
    category=["conductivity", "deck"],
    description="电导率工站的三层 2×5 固定料架。",
    icon="conductivity-testing-workstation.webp",
)
class ConductivityStationDeck(Deck):
    def __init__(
        self,
        name: str = "conductivity_station_deck",
        size_x: float = 900.0,
        size_y: float = 900.0,
        size_z: float = 400.0,
        category: str = "deck",
        setup: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            category=category,
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
            LAYER_WAREHOUSE_NAMES[layer]: conductivity_rack_layer(
                LAYER_WAREHOUSE_NAMES[layer], list(SLOT_LABELS)
            )
            for layer in RACK_LAYERS
        }
        self.warehouse_locations = {
            LAYER_WAREHOUSE_NAMES["mold"]: Coordinate(0.0, 0.0, 160.0),
            LAYER_WAREHOUSE_NAMES["bottle"]: Coordinate(0.0, LAYER_Y_PITCH, 80.0),
            LAYER_WAREHOUSE_NAMES["funnel"]: Coordinate(
                0.0, LAYER_Y_PITCH * 2, 0.0
            ),
        }
        for layer in ("mold", "bottle", "funnel"):
            name = LAYER_WAREHOUSE_NAMES[layer]
            self.assign_child_resource(self.warehouses[name], self.warehouse_locations[name])

    def get_site(self, warehouse_name: str, label: str) -> Any:
        return self.warehouses[warehouse_name][label]


__all__ = ["ConductivityStationDeck"]
