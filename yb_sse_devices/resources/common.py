"""合成工站的资源模板与可追踪库存架。

这些定义描述的是设备包中的**资源类型**，不是某件现场物料。现场实例
（条码、批次和当前库位）放在 deployment graph 中。这样工作流可以通过
``ResourceSlot`` 传递资源，PLC 动作完成后再由 OS 更新资源位置。
"""

from __future__ import annotations

from typing import Any

try:
    from pylabrobot.resources import Container, Coordinate, Deck
except Exception:  # pragma: no cover - 允许脱离 Uni-Lab OS 运行单测
    class Container:  # type: ignore[no-redef]
        def __init__(
            self,
            name: str,
            size_x: float = 40.0,
            size_y: float = 40.0,
            size_z: float = 40.0,
            **kwargs: Any,
        ) -> None:
            self.name = name
            self._size_x = size_x
            self._size_y = size_y
            self._size_z = size_z
            self.kwargs = kwargs

    class Coordinate:  # type: ignore[no-redef]
        def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
            self.x, self.y, self.z = x, y, z

    class Deck:  # type: ignore[no-redef]
        def __init__(
            self,
            size_x: float = 900.0,
            size_y: float = 900.0,
            size_z: float = 400.0,
            name: str = "deck",
            category: str = "deck",
            **kwargs: Any,
        ) -> None:
            self.name = name
            self.children: list[Any] = []
            self.kwargs = kwargs

        def assign_child_resource(self, resource: Any, location: Any = None) -> None:
            del location
            self.children.append(resource)


class ConductivityPlaceholder(Container):
    """Small labware placeholder used by the conductivity station resources."""

    resource_id = "conductivity_placeholder"

    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 70.0)
        kwargs.setdefault("size_y", 70.0)
        kwargs.setdefault("size_z", 40.0)
        kwargs.setdefault("category", "container")
        super().__init__(name=name, **kwargs)


class FallbackWarehouse:
    """A ten-slot fallback used when pylabrobot/Uni-Lab is unavailable."""

    def __init__(self, name: str, labels: list[str]) -> None:
        self.name = name
        self.sites = {label: None for label in labels}

    def __getitem__(self, key: str) -> Any:
        return self.sites[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.sites[key] = value


def conductivity_rack_layer(name: str, labels: list[str]) -> Any:
    """Create one fixed 2×5 conductivity rack with stable slot labels."""

    try:
        from unilabos.resources.warehouse import warehouse_factory

        return warehouse_factory(
            name=name,
            num_items_x=5,
            num_items_y=2,
            num_items_z=1,
            dx=10.0,
            dy=10.0,
            dz=10.0,
            item_dx=80.0,
            item_dy=80.0,
            item_dz=50.0,
            resource_size_x=70.0,
            resource_size_y=70.0,
            resource_size_z=40.0,
            category="warehouse",
            layout="row-major",
        )
    except Exception:  # pragma: no cover - pure-Python test fallback
        return FallbackWarehouse(name, labels)

try:
    from unilabos.registry.decorators import resource
except Exception:  # pragma: no cover - 仅供文档构建环境
    def resource(*_args: Any, **_kwargs: Any):
        def decorator(obj: Any) -> Any:
            return obj

        return decorator


# Site labels are part of the package contract. The deployment graph may add
# occupancy and barcode state, but it must not invent a different inventory
# shape at runtime. The suffix is zero based: ``-0`` maps to PLC slot 1.
SYNTHESIS_POWDER_RACK_AVAILABLE_SITES = [
    {"label": "synthesis_powder_rack_01-0", "position": {"x": 80.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-1", "position": {"x": 160.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-2", "position": {"x": 240.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-3", "position": {"x": 320.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-4", "position": {"x": 400.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-5", "position": {"x": 480.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-6", "position": {"x": 560.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-7", "position": {"x": 640.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-8", "position": {"x": 720.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
    {"label": "synthesis_powder_rack_01-9", "position": {"x": 800.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_powder"]},
]
SYNTHESIS_CRUCIBLE_RACK_AVAILABLE_SITES = [
    {"label": "synthesis_crucible_rack_01-0", "position": {"x": 80.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-1", "position": {"x": 160.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-2", "position": {"x": 240.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-3", "position": {"x": 320.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-4", "position": {"x": 400.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-5", "position": {"x": 480.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-6", "position": {"x": 560.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-7", "position": {"x": 640.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-8", "position": {"x": 720.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
    {"label": "synthesis_crucible_rack_01-9", "position": {"x": 800.0, "y": 80.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 70.0}, "content_type": ["synthesis_crucible"]},
]
SYNTHESIS_BEAD_RACK_AVAILABLE_SITES = [
    {"label": "synthesis_bead_rack_01-0", "position": {"x": 80.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_bead_bottle"]},
    {"label": "synthesis_bead_rack_01-1", "position": {"x": 160.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_bead_bottle"]},
    {"label": "synthesis_bead_rack_01-2", "position": {"x": 240.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_bead_bottle"]},
    {"label": "synthesis_bead_rack_01-3", "position": {"x": 320.0, "y": 80.0, "z": 0.0}, "size": {"width": 70.0, "height": 70.0, "depth": 50.0}, "content_type": ["synthesis_bead_bottle"]},
]
SYNTHESIS_TRAY_RACK_AVAILABLE_SITES = [
    {"label": "synthesis_tray_rack_01-0", "position": {"x": 100.0, "y": 100.0, "z": 0.0}, "size": {"width": 420.0, "height": 300.0, "depth": 35.0}, "content_type": ["synthesis_tray"]},
    {"label": "synthesis_tray_rack_01-1", "position": {"x": 240.0, "y": 100.0, "z": 0.0}, "size": {"width": 420.0, "height": 300.0, "depth": 35.0}, "content_type": ["synthesis_tray"]},
    {"label": "synthesis_tray_rack_01-2", "position": {"x": 380.0, "y": 100.0, "z": 0.0}, "size": {"width": 420.0, "height": 300.0, "depth": 35.0}, "content_type": ["synthesis_tray"]},
    {"label": "synthesis_tray_rack_01-3", "position": {"x": 520.0, "y": 100.0, "z": 0.0}, "size": {"width": 420.0, "height": 300.0, "depth": 35.0}, "content_type": ["synthesis_tray"]},
]


class _SynthesisContainer(Container):
    """统一尺寸和元数据的合成物料容器基类。"""

    resource_id = "synthesis_container"

    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 70.0)
        kwargs.setdefault("size_y", 70.0)
        kwargs.setdefault("size_z", 90.0)
        kwargs.setdefault("category", "container")
        super().__init__(name=name, **kwargs)
        # pylabrobot 资源允许动态扩展属性；前端用此字段展示业务类型。
        extra = dict(getattr(self, "unilabos_extra", None) or {})
        extra.setdefault("content_type", [self.resource_id])
        self.unilabos_extra = extra



def _warehouse(
    name: str,
    *,
    content_type: str,
    num_items_x: int = 5,
    num_items_y: int = 2,
    num_items_z: int = 1,
) -> Any:
    """创建固定编号的仓库，并把允许物料类型写入每个 Site。"""

    try:
        from unilabos.resources.warehouse import warehouse_factory

        warehouse = warehouse_factory(
            name=name,
            num_items_x=num_items_x,
            num_items_y=num_items_y,
            num_items_z=num_items_z,
            dx=10.0,
            dy=10.0,
            dz=10.0,
            item_dx=80.0,
            item_dy=80.0,
            item_dz=60.0,
            resource_size_x=70.0,
            resource_size_y=70.0,
            resource_size_z=50.0,
            category="warehouse",
            layout="row-major",
        )
        sites = list(getattr(warehouse, "sites", []) or [])
        for site in sites:
            extra = dict(getattr(site, "unilabos_extra", None) or {})
            extra["content_type"] = [content_type]
            site.unilabos_extra = extra
        return warehouse
    except Exception:  # pragma: no cover - fallback 供纯 Python 单测
        return _FallbackWarehouse(name, content_type, num_items_x * num_items_y * num_items_z)


def _apply_site_config(warehouse: Any, sites: list[Any] | None) -> Any:
    """Apply graph site metadata without making graph JSON a second resource model."""

    if not sites:
        return warehouse
    by_label = {}
    for site in list(getattr(warehouse, "sites", []) or []):
        label = str(getattr(site, "name", ""))
        by_label[label] = site
    for item in sites:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", item.get("name", "")))
        target = by_label.get(label)
        if target is None:
            continue
        extra = dict(getattr(target, "unilabos_extra", None) or {})
        for key in ("content_type", "visible", "occupied_by"):
            if key in item:
                extra[key] = item[key]
        target.unilabos_extra = extra
    return warehouse

class _FallbackSite:
    def __init__(self, name: str, content_type: str) -> None:
        self.name = name
        self.unilabos_extra = {"content_type": [content_type]}
        self.resource = None


class _FallbackWarehouse:
    def __init__(self, name: str, content_type: str, count: int) -> None:
        self.name = name
        self.sites = [_FallbackSite(f"{name}_{i + 1:02d}", content_type) for i in range(count)]

    def __getitem__(self, index: int) -> Any:
        return self.sites[index]


# Conductivity rack helpers are kept here as shared implementation.
from yb_sse_devices.protocol import SLOT_LABELS
LAYER_Y_PITCH = 240.0

def conductivity_rack_layer(name: str, labels: list[str] | None = None) -> Any:
    """Create one 2 x 5 conductivity rack layer (A01-B05)."""
    try:
        from unilabos.resources.warehouse import warehouse_factory
    except Exception:  # pragma: no cover
        return _FallbackConductivityWarehouse(name, labels or list(SLOT_LABELS))
    return warehouse_factory(
        name=name, num_items_x=5, num_items_y=2, num_items_z=1,
        dx=10.0, dy=10.0, dz=10.0, item_dx=80.0, item_dy=80.0,
        item_dz=50.0, resource_size_x=70.0, resource_size_y=70.0,
        resource_size_z=40.0, category="warehouse", layout="row-major",
    )

class _FallbackConductivityWarehouse:
    def __init__(self, name: str, labels: list[str] | None = None) -> None:
        self.name = name
        self.sites = {label: None for label in (labels or SLOT_LABELS)}
    def __getitem__(self, key: str) -> Any:
        return self.sites[key]
    def __setitem__(self, key: str, resource: Any) -> None:
        self.sites[key] = resource

class ConductivityPlaceholder(Container):
    """Logical placeholder for conductivity labware until dimensions are confirmed."""
    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 70.0)
        kwargs.setdefault("size_y", 70.0)
        kwargs.setdefault("size_z", 40.0)
        kwargs.setdefault("category", "container")
        super().__init__(name, **kwargs)
