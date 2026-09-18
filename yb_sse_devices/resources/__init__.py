"""YB 设备包资源模板：电导工站与合成工站物料、料架、Deck。"""

from typing import Any

__all__ = [
    "ConductivityStation_Deck",
    "ConductivityFunnel",
    "ConductivityMold",
    "ConductivitySinteringBottle",
    "conductivity_rack_layer",
    "SynthesisStation_Deck",
    "SynthesisPowder",
    "SynthesisCrucible",
    "SynthesisBeadBottle",
    "SynthesisTray",
    "synthesis_powder_rack",
    "synthesis_crucible_rack",
    "synthesis_tray_rack",
    "synthesis_bead_rack",
    "YBSynthesisDeck",
]

_EXPORTS = {
    "ConductivityStation_Deck": (".decks", "ConductivityStation_Deck"),
    "ConductivityFunnel": (".materials", "ConductivityFunnel"),
    "ConductivityMold": (".materials", "ConductivityMold"),
    "ConductivitySinteringBottle": (".materials", "ConductivitySinteringBottle"),
    "conductivity_rack_layer": (".warehouses", "conductivity_rack_layer"),
    "SynthesisStation_Deck": (".synthesis_deck", "SynthesisStation_Deck"),
    "SynthesisPowder": (".synthesis_resources", "SynthesisPowder"),
    "SynthesisCrucible": (".synthesis_resources", "SynthesisCrucible"),
    "SynthesisBeadBottle": (".synthesis_resources", "SynthesisBeadBottle"),
    "SynthesisTray": (".synthesis_resources", "SynthesisTray"),
    "synthesis_powder_rack": (".synthesis_resources", "synthesis_powder_rack"),
    "synthesis_crucible_rack": (".synthesis_resources", "synthesis_crucible_rack"),
    "synthesis_tray_rack": (".synthesis_resources", "synthesis_tray_rack"),
    "synthesis_bead_rack": (".synthesis_resources", "synthesis_bead_rack"),
    "YBSynthesisDeck": (".synthesis_resources", "YBSynthesisDeck"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = _EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr)
    globals()[name] = value
    return value
