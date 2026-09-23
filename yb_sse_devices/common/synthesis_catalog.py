"""Operator-facing catalog for the YB synthesis station.

The PLC only accepts numeric codes, while the operator-facing workflow should
use the same names shown by the legacy station UI.  This module is the device
package source of truth for those names and their PLC codes.  It intentionally
does *not* contain physical powder-rack or return-position numbers: those
locations are installation-specific and remain a required direct-PLC
configuration until the station mapping is confirmed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from yb_sse_devices.common.synthesis_protocol import DEFAULT_RECIPE_MATERIALS


# Keep the labels exactly as they appear in the existing HMI.  They are valid
# workflow enum values, so OS can render them without maintaining a duplicate
# hard-coded list of PLC integers.
PALLET_SPECS: dict[str, dict[str, Any]] = {
    "4 槽位托盘": {"code": 1, "slot_count": 4, "cubic_types": (3, 4, 5)},
    "5 槽位托盘": {"code": 2, "slot_count": 5, "cubic_types": (2,)},
    "6 槽位托盘": {"code": 3, "slot_count": 6, "cubic_types": (1,)},
}

CUBIC_SPECS: dict[str, dict[str, Any]] = {
    "Al2O3 30*30": {"code": 1, "pallets": ("6 槽位托盘",)},
    "Al2O3 35*40": {"code": 2, "pallets": ("5 槽位托盘",)},
    "Al2O3 40*40": {"code": 3, "pallets": ("4 槽位托盘",)},
    "ZrO2 40*35": {"code": 4, "pallets": ("4 槽位托盘",)},
    "ZrO2 40*46": {"code": 5, "pallets": ("4 槽位托盘",)},
}


def _material_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row["name"]),
            "weight": float(row.get("weight", 0.0)),
            "tolerance": float(row.get("tolerance", 0.0)),
            "pre_add": bool(row.get("pre_add", False)),
        }
        for row in rows
    ]


# Recipe metadata belongs to the package because it is consumed by both the
# device action and the workflow contract.  Rack positions are deliberately
# absent; see ``physical_mappings`` below.
RECIPE_SPECS: dict[str, dict[str, Any]] = {
    "YB-LiCl-P2S5-2G": {
        "formula": "LiCl-P2S5",
        "synthesis_mass": 4.0,
        "n_ball_bead": 0,
        "materials": _material_rows(
            [
                {"name": "LiCl", "weight": 2.0, "tolerance": 0.0007},
                {"name": "P2S5", "weight": 2.0, "tolerance": 0.0007},
            ]
        ),
    },
    # Existing simulator/atomic workflows keep their original defaults.
    "YB-SIM-Li6PS5Cl": {
        "formula": "Li6PS5Cl",
        "synthesis_mass": 3.86,
        "n_ball_bead": 80,
        "materials": _material_rows([dict(item) for item in DEFAULT_RECIPE_MATERIALS]),
    },
}


# A visible marker is useful for diagnostics and future configuration UIs,
# without pretending that an unconfirmed number is a physical PLC mapping.
PHYSICAL_MAPPINGS: dict[str, None] = {
    "powder_rack_positions": None,
    "crucible_return_positions": None,
}


def _lookup(value: int | str, table: dict[str, dict[str, Any]], label: str) -> dict[str, Any]:
    if isinstance(value, str):
        key = value.strip()
        if key in table:
            return table[key]
        # Accept numeric strings for backwards-compatible callers.
        try:
            numeric = int(key)
        except ValueError:
            numeric = -1
    else:
        numeric = value
    for spec in table.values():
        if int(spec["code"]) == int(numeric):
            return spec
    raise ValueError(f"未知{label}: {value!r}")


def pallet_type_code(value: int | str) -> int:
    """Resolve the operator label (or legacy integer) to PLC pallet code."""

    return int(_lookup(value, PALLET_SPECS, "托盘类型")["code"])


def pallet_type_label(value: int | str) -> str:
    if isinstance(value, str) and value.strip() in PALLET_SPECS:
        return value.strip()
    code = pallet_type_code(value)
    return next(label for label, spec in PALLET_SPECS.items() if int(spec["code"]) == code)


def cubic_type_code(value: int | str) -> int:
    return int(_lookup(value, CUBIC_SPECS, "坩埚类型")["code"])


def cubic_type_label(value: int | str) -> str:
    if isinstance(value, str) and value.strip() in CUBIC_SPECS:
        return value.strip()
    code = cubic_type_code(value)
    return next(label for label, spec in CUBIC_SPECS.items() if int(spec["code"]) == code)


def recipe_spec(recipe_name: str) -> dict[str, Any]:
    """Return a defensive copy of package-owned recipe metadata."""

    name = str(recipe_name or "").strip()
    try:
        spec = RECIPE_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"设备包没有配方: {name or '<空>'}") from exc
    return deepcopy(spec)


def recipe_names() -> tuple[str, ...]:
    return tuple(RECIPE_SPECS)


def pallet_names() -> tuple[str, ...]:
    return tuple(PALLET_SPECS)


def cubic_names() -> tuple[str, ...]:
    return tuple(CUBIC_SPECS)


def compatible_cubic_names(pallet_type: int | str) -> tuple[str, ...]:
    pallet = pallet_type_label(pallet_type)
    return tuple(
        name for name, spec in CUBIC_SPECS.items() if pallet in tuple(spec["pallets"])
    )


def derive_pallet_slot_types(
    pallet_type: int | str,
    cubic_type: int | str,
    task_slot_nums: list[int] | None,
) -> list[int]:
    """Build PLC command-7's six slot-type words from operator choices.

    Unselected slots are encoded as zero.  The selected slot values are the
    PLC cubic type code, not the operator's task slot numbers.
    """

    pallet_label = pallet_type_label(pallet_type)
    cubic_label = cubic_type_label(cubic_type)
    # Numeric arguments are retained for legacy atomic/device callers.  The
    # operator-facing workflow uses labels and gets the compatibility check;
    # old simulator graphs historically allowed arbitrary code combinations.
    operator_labels = (
        (isinstance(pallet_type, str) and not pallet_type.strip().isdigit())
        or (isinstance(cubic_type, str) and not cubic_type.strip().isdigit())
    )
    if operator_labels and cubic_label not in compatible_cubic_names(pallet_label):
        raise ValueError(f"{pallet_label}不支持坩埚类型 {cubic_label}")
    count = int(PALLET_SPECS[pallet_label]["slot_count"])
    slots = [int(value) for value in (task_slot_nums or [])]
    if not slots:
        raise ValueError("至少需要一个任务槽位")
    if len(set(slots)) != len(slots) or any(value < 1 or value > count for value in slots):
        raise ValueError(f"任务槽位必须在 1..{count} 范围内且不能重复")
    result = [0] * 6
    cubic_code = cubic_type_code(cubic_label)
    for slot in slots:
        result[slot - 1] = cubic_code
    return result


def default_recipe_inputs(recipe_name: str) -> dict[str, Any]:
    """Flatten a recipe spec into the legacy device action columns."""

    try:
        spec = recipe_spec(recipe_name)
    except ValueError:
        # Legacy atomic adapters may intentionally upload a one-off recipe
        # name while supplying their own columns later.  Preserve the old
        # simulator defaults for that compatibility path; the operator
        # workflow itself only offers names from ``RECIPE_SPECS``.
        spec = RECIPE_SPECS["YB-SIM-Li6PS5Cl"]
    materials = list(spec["materials"])
    return {
        "formula": str(spec["formula"]),
        "synthesis_mass": float(spec["synthesis_mass"]),
        "n_ball_bead": int(spec["n_ball_bead"]),
        "powder_names": [str(item["name"]) for item in materials],
        "powder_weights": [float(item["weight"]) for item in materials],
        "powder_tolerances": [float(item["tolerance"]) for item in materials],
        "powder_pre_adds": [bool(item["pre_add"]) for item in materials],
    }


__all__ = [
    "CUBIC_SPECS",
    "PALLET_SPECS",
    "PHYSICAL_MAPPINGS",
    "RECIPE_SPECS",
    "compatible_cubic_names",
    "cubic_names",
    "cubic_type_code",
    "cubic_type_label",
    "default_recipe_inputs",
    "derive_pallet_slot_types",
    "pallet_names",
    "pallet_type_code",
    "pallet_type_label",
    "recipe_names",
    "recipe_spec",
]
