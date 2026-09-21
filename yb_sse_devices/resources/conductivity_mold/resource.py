"""Canonical conductivity mold resource."""

from __future__ import annotations

from unilabos.registry.decorators import resource

from ..common import ConductivityPlaceholder


@resource(
    id="conductivity_mold",
    displayname="电导测试模具",
    category=["conductivity", "labware", "container"],
    description="电导率工站使用的测试模具。",
)
class ConductivityMold(ConductivityPlaceholder):
    resource_id = "conductivity_mold"


__all__ = ["ConductivityMold"]
