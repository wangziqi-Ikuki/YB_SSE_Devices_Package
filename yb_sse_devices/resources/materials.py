"""Compatibility imports for the legacy flat resource module."""
from .conductivity_funnel.resource import ConductivityFunnel
from .conductivity_mold.resource import ConductivityMold
from .conductivity_sintering_bottle.resource import ConductivitySinteringBottle

LAYER_RESOURCE_CLASSES = {
    "funnel": ConductivityFunnel,
    "bottle": ConductivitySinteringBottle,
    "mold": ConductivityMold,
}
__all__ = ["ConductivityFunnel", "ConductivityMold", "ConductivitySinteringBottle", "LAYER_RESOURCE_CLASSES"]
