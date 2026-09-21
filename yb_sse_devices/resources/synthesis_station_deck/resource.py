"""Canonical empty synthesis station deck used by the TCP adapter."""

from __future__ import annotations

from unilabos.registry.decorators import resource

from typing import Any

from ..common import Deck


@resource(
    id="synthesis_station_deck",
    displayname="合成工站台面",
    category=["synthesis", "deck"],
    description="TCP 合成工站的台面状态资源。",
    icon="synthesis_station.webp",
)
class SynthesisStationDeck(Deck):
    def __init__(
        self,
        name: str = "synthesis_station_deck",
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
        self.unilabos_extra: dict[str, Any] = {
            "icon": "synthesis_station.webp",
            "unilabos_resource_class": "SynthesisStation_Deck",
        }
        if setup:
            self.setup()

    def setup(self) -> None:
        self.warehouses = self.warehouses or {}
        self.warehouse_locations = self.warehouse_locations or {}

    def apply_status(self, snapshot: dict[str, Any]) -> None:
        extra = dict(getattr(self, "unilabos_extra", None) or {})
        extra.update(snapshot)
        self.unilabos_extra = extra


__all__ = ["SynthesisStationDeck"]
