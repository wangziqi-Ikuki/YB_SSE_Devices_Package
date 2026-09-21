"""YB 固态电解质实验室外部设备包。"""

from .devices.conductivity_station.device import ConductivityStation
from .devices.synthesis_station.device import SynthesisStation
from .devices.yb_synthesis_atomic_station.device import YBSynthesisAtomicStation
from .devices.yb_synthesis_modbus_station.device import YBSynthesisModbusStation
from .devices.yb_synthesis_modbus_station.controller import SynthesisDirectController
from .resources import (
    ConductivityFunnel,
    ConductivityMold,
    ConductivitySinteringBottle,
    ConductivityStationDeck,
    SynthesisBeadBottle,
    SynthesisCrucible,
    SynthesisPowder,
    SynthesisStationDeck,
    SynthesisTray,
    YBSynthesisDeck,
    synthesis_bead_rack,
    synthesis_crucible_rack,
    synthesis_powder_rack,
    synthesis_tray_rack,
)

# Backwards-compatible public names used by existing workflow code.
ConductivityStation_Deck = ConductivityStationDeck
SynthesisStation_Deck = SynthesisStationDeck

__all__ = [
    "ConductivityStation", "SynthesisStation", "YBSynthesisModbusStation",
    "YBSynthesisAtomicStation", "SynthesisDirectController",
    "ConductivityStationDeck", "ConductivityStation_Deck",
    "ConductivityFunnel", "ConductivityMold", "ConductivitySinteringBottle",
    "SynthesisStationDeck", "SynthesisStation_Deck", "SynthesisPowder",
    "SynthesisCrucible", "SynthesisBeadBottle", "SynthesisTray",
    "YBSynthesisDeck", "synthesis_bead_rack", "synthesis_crucible_rack",
    "synthesis_powder_rack", "synthesis_tray_rack",
]
