"""YB 固态电解质实验室外部设备包。"""

from typing import Any

__all__ = [
    "ConductivityStation",
    "ConductivityStation_Deck",
    "ConductivityFunnel",
    "ConductivityMold",
    "ConductivitySinteringBottle",
    "SynthesisStation",
    "SynthesisStation_Deck",
]

_EXPORTS = {
    "ConductivityStation": (".conductivity", "ConductivityStation"),
    "ConductivityStation_Deck": (".resources.decks", "ConductivityStation_Deck"),
    "ConductivityFunnel": (".resources.materials", "ConductivityFunnel"),
    "ConductivityMold": (".resources.materials", "ConductivityMold"),
    "ConductivitySinteringBottle": (
        ".resources.materials",
        "ConductivitySinteringBottle",
    ),
    "SynthesisStation": (".synthesis_station", "SynthesisStation"),
    "SynthesisStation_Deck": (".resources.synthesis_deck", "SynthesisStation_Deck"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = _EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr)
    globals()[name] = value
    return value
