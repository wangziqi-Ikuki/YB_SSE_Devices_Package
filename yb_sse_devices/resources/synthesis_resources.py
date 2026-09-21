"""Compatibility imports for the legacy flat synthesis resource module."""
from .synthesis_bead_bottle.resource import SynthesisBeadBottle
from .synthesis_bead_rack.resource import synthesis_bead_rack
from .synthesis_crucible.resource import SynthesisCrucible
from .synthesis_crucible_rack.resource import synthesis_crucible_rack
from .synthesis_powder.resource import SynthesisPowder
from .synthesis_powder_rack.resource import synthesis_powder_rack
from .synthesis_tray.resource import SynthesisTray
from .synthesis_tray_rack.resource import synthesis_tray_rack
from .yb_synthesis_deck.resource import YBSynthesisDeck
__all__ = ["SynthesisBeadBottle", "synthesis_bead_rack", "SynthesisCrucible", "synthesis_crucible_rack", "SynthesisPowder", "synthesis_powder_rack", "SynthesisTray", "synthesis_tray_rack", "YBSynthesisDeck"]
