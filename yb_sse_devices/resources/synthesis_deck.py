"""合成工站 Deck：先预留空台面，仅挂背景图与状态，槽位暂不实现。"""

from __future__ import annotations

from typing import Any

from yb_sse_devices.synthesis_protocol import DECK_ICON

try:
    from pylabrobot.resources import Deck
except Exception:  # pragma: no cover
    class Deck:  # type: ignore[no-redef]
        def __init__(
            self,
            name: str,
            size_x: float = 0,
            size_y: float = 0,
            size_z: float = 0,
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
except Exception:  # pragma: no cover
    def resource(*_args: Any, **_kwargs: Any):
        def decorator(cls):
            return cls

        return decorator


@resource(
    id="SynthesisStation_Deck",
    category=["deck"],
    description="合成工站 Deck（先预留：背景图 + 状态，槽位暂空）",
    icon=DECK_ICON,
)
class SynthesisStation_Deck(Deck):
    def __init__(
        self,
        name: str = "SynthesisStation_Deck",
        size_x: float = 900.0,
        size_y: float = 900.0,
        size_z: float = 400.0,
        category: str = "deck",
        setup: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, **kwargs
        )
        extra = dict(getattr(self, "unilabos_extra", None) or {})
        extra.setdefault("unilabos_resource_class", "SynthesisStation_Deck")
        extra.setdefault("icon", DECK_ICON)
        self.unilabos_extra = extra
        self.warehouses: dict[str, Any] = {}
        self.warehouse_locations: dict[str, Any] = {}
        if setup:
            self.setup()

    def setup(self) -> None:
        """本阶段只建空台面，不创建托盘或坩埚库位。"""
        if self.warehouses is None:
            self.warehouses = {}
        if self.warehouse_locations is None:
            self.warehouse_locations = {}

    def apply_status(self, snapshot: dict[str, Any]) -> None:
        extra = dict(getattr(self, "unilabos_extra", None) or {})
        extra["unilabos_resource_class"] = "SynthesisStation_Deck"
        extra["icon"] = DECK_ICON
        extra.update(snapshot)
        self.unilabos_extra = extra
