"""YB 设备包资源模板。

每个可注册资源的实现位于 ``resources/<snake_case_id>/resource.py``；本文件
只提供稳定的兼容导出，不再承载资源注册装饰器。
"""

from .conductivity_funnel.resource import ConductivityFunnel
from .conductivity_mold.resource import ConductivityMold
from .conductivity_sintering_bottle.resource import ConductivitySinteringBottle
from .conductivity_station_deck.resource import ConductivityStationDeck
from .synthesis_bead_bottle.resource import SynthesisBeadBottle
from .synthesis_bead_rack.resource import synthesis_bead_rack
from .synthesis_crucible.resource import SynthesisCrucible
from .synthesis_crucible_rack.resource import synthesis_crucible_rack
from .synthesis_powder.resource import SynthesisPowder
from .synthesis_powder_rack.resource import synthesis_powder_rack
from .synthesis_station_deck.resource import SynthesisStationDeck
from .synthesis_tray.resource import SynthesisTray
from .synthesis_tray_rack.resource import synthesis_tray_rack
from .yb_synthesis_deck.resource import YBSynthesisDeck

# Legacy names remain source compatible; their metadata now uses the canonical
# snake_case IDs above.
ConductivityStation_Deck = ConductivityStationDeck
SynthesisStation_Deck = SynthesisStationDeck

__all__ = [
    "ConductivityStationDeck", "ConductivityStation_Deck",
    "ConductivityFunnel", "ConductivityMold", "ConductivitySinteringBottle",
    "SynthesisStationDeck", "SynthesisStation_Deck", "SynthesisPowder",
    "SynthesisCrucible", "SynthesisBeadBottle", "SynthesisTray",
    "synthesis_powder_rack", "synthesis_crucible_rack", "synthesis_tray_rack",
    "synthesis_bead_rack", "YBSynthesisDeck",
]
