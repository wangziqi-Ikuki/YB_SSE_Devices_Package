"""合成工站工作流表单类型。

本文件不用 from __future__ import annotations，以便注册表把 TypedDict
展开成带 properties 的 JSON Schema，前端才能显示槽位/物料输入框。
"""

from typing import TypedDict


class RecipeMaterial(TypedDict):
    name: str
    weight: float
    tolerance: float
    pre_add: bool


class TaskSlot(TypedDict):
    slot_num: int
    recipe_name: str
