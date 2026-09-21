"""Canonical conductivity sintering bottle resource."""

from __future__ import annotations

from unilabos.registry.decorators import resource

from ..common import ConductivityPlaceholder


@resource(
    id="conductivity_sintering_bottle",
    displayname="电导测试烧结料瓶",
    category=["conductivity", "labware", "container"],
    description="电导率工站使用的烧结料瓶。",
)
class ConductivitySinteringBottle(ConductivityPlaceholder):
    resource_id = "conductivity_sintering_bottle"


__all__ = ["ConductivitySinteringBottle"]
