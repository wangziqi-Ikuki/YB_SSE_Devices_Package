"""YB 设备包资源模板。

每个可注册资源的实现位于 ``resources/<snake_case_id>/resource.py``；本文件
只提供稳定的兼容导出，不再承载资源注册装饰器。
"""

from .synthesis_bead_bottle.resource import SynthesisBeadBottle
from .synthesis_bead_rack.resource import synthesis_bead_rack
from .synthesis_crucible.resource import SynthesisCrucible
from .synthesis_crucible_rack.resource import synthesis_crucible_rack
from .synthesis_powder.resource import SynthesisPowder
from .synthesis_powder_rack.resource import synthesis_powder_rack
from .synthesis_tray.resource import SynthesisTray
from .synthesis_tray_rack.resource import synthesis_tray_rack
from .yb_synthesis_deck.resource import YBSynthesisDeck

__all__ = [
    "SynthesisPowder", "SynthesisCrucible", "SynthesisBeadBottle",
    "SynthesisTray", "synthesis_powder_rack", "synthesis_crucible_rack",
    "synthesis_tray_rack", "synthesis_bead_rack", "YBSynthesisDeck",
]
