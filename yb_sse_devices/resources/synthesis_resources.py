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

try:
    from unilabos.registry.decorators import resource
except Exception:  # pragma: no cover - 仅供文档构建环境
    def resource(*_args: Any, **_kwargs: Any):
        def decorator(obj: Any) -> Any:
            return obj

        return decorator


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


@resource(
    id="synthesis_powder",
    displayname="合成粉料盒",
    category=["synthesis", "powder", "container"],
    description="用于合成工站称粉的单批粉料容器；实例条码和批次由启动图提供。",
)
class SynthesisPowder(_SynthesisContainer):
    resource_id = "synthesis_powder"


@resource(
    id="synthesis_crucible",
    displayname="合成坩埚",
    category=["synthesis", "crucible", "container"],
    description="由机械臂夹取并在 PLC 内部扫码绑定的合成坩埚。",
)
class SynthesisCrucible(_SynthesisContainer):
    resource_id = "synthesis_crucible"

    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 90.0)
        kwargs.setdefault("size_y", 90.0)
        kwargs.setdefault("size_z", 70.0)
        super().__init__(name=name, **kwargs)


@resource(
    id="synthesis_bead_bottle",
    displayname="合成磨球瓶",
    category=["synthesis", "bead", "container"],
    description="合成工艺加珠步骤使用的磨球瓶。",
)
class SynthesisBeadBottle(_SynthesisContainer):
    resource_id = "synthesis_bead_bottle"


@resource(
    id="synthesis_tray",
    displayname="合成托盘",
    category=["synthesis", "tray", "holder"],
    description="装载坩埚和粉料瓶、供机械臂搬运的合成托盘。",
)
class SynthesisTray(_SynthesisContainer):
    resource_id = "synthesis_tray"

    def __init__(self, name: str, **kwargs: Any) -> None:
        kwargs.setdefault("size_x", 420.0)
        kwargs.setdefault("size_y", 300.0)
        kwargs.setdefault("size_z", 35.0)
        super().__init__(name=name, **kwargs)


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


@resource(
    id="synthesis_powder_rack",
    displayname="合成粉料架",
    category=["synthesis", "warehouse", "powder"],
    description="合成粉料盒的固定 2×5 库位架；每个库位只允许 synthesis_powder。",
)
def synthesis_powder_rack(name: str = "synthesis_powder_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(_warehouse(name, content_type="synthesis_powder"), sites)


@resource(
    id="synthesis_crucible_rack",
    displayname="合成坩埚架",
    category=["synthesis", "warehouse", "crucible"],
    description="合成坩埚的固定 2×5 库位架；每个库位只允许 synthesis_crucible。",
)
def synthesis_crucible_rack(name: str = "synthesis_crucible_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(_warehouse(name, content_type="synthesis_crucible"), sites)


@resource(
    id="synthesis_tray_rack",
    displayname="合成托盘架",
    category=["synthesis", "warehouse", "tray"],
    description="合成托盘的固定库位架；每个库位只允许 synthesis_tray。",
)
def synthesis_tray_rack(name: str = "synthesis_tray_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(
        _warehouse(name, content_type="synthesis_tray", num_items_x=2, num_items_y=2), sites
    )


@resource(
    id="synthesis_bead_rack",
    displayname="合成磨球瓶架",
    category=["synthesis", "warehouse", "bead"],
    description="合成磨球瓶的固定库位架；每个库位只允许 synthesis_bead_bottle。",
)
def synthesis_bead_rack(name: str = "synthesis_bead_rack", sites: list[Any] | None = None) -> Any:
    return _apply_site_config(
        _warehouse(name, content_type="synthesis_bead_bottle", num_items_x=2, num_items_y=2), sites
    )


@resource(
    id="yb_synthesis_deck",
    displayname="YB 合成工站台面",
    category=["synthesis", "deck"],
    description="合成工站的资源根节点，包含粉料、坩埚和托盘仓库。",
    icon="synthesis_station.webp",
)
class YBSynthesisDeck(Deck):
    """带有真实资源树的合成工站台面。

    旧的 ``SynthesisStation_Deck`` 保留为空台面以兼容旧 Qt/TCP 测试；
    新的 Modbus 直连启动图使用此资源根节点。
    """

    def __init__(
        self,
        name: str = "yb_synthesis_deck",
        size_x: float = 1200.0,
        size_y: float = 900.0,
        size_z: float = 500.0,
        setup: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(size_x=size_x, size_y=size_y, size_z=size_z, name=name, category="deck")
        self.warehouses: dict[str, Any] = {}
        self.warehouse_locations: dict[str, Any] = {}
        if setup:
            self.warehouses = {
                "powder_rack": synthesis_powder_rack("synthesis_powder_rack"),
                "crucible_rack": synthesis_crucible_rack("synthesis_crucible_rack"),
                "tray_rack": synthesis_tray_rack("synthesis_tray_rack"),
                "bead_rack": synthesis_bead_rack("synthesis_bead_rack"),
            }
            for index, rack in enumerate(self.warehouses.values()):
                # pylabrobot requires an explicit child location; graph nodes may
                # override these positions at deployment time.
                self.assign_child_resource(
                    rack,
                    location=Coordinate(x=50.0, y=80.0 + index * 260.0, z=0.0),
                )
        extra = dict(getattr(self, "unilabos_extra", None) or {})
        extra.update({"unilabos_resource_class": "yb_synthesis_deck", "icon": "synthesis_station.webp"})
        self.unilabos_extra = extra


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


__all__ = [
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
