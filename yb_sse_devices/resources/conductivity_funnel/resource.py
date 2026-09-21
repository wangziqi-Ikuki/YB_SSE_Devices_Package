"""Canonical conductivity funnel resource."""

from __future__ import annotations

from unilabos.registry.decorators import resource

from ..common import ConductivityPlaceholder


@resource(
    id="conductivity_funnel",
    displayname="电导测试漏斗",
    category=["conductivity", "labware", "container"],
    description="电导率工站使用的漏斗。",
)
class ConductivityFunnel(ConductivityPlaceholder):
    resource_id = "conductivity_funnel"


__all__ = ["ConductivityFunnel"]
