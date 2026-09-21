"""Bundled legacy JSON workflow exports for compatibility and migration tests."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

WORKFLOW_FILES: dict[str, str] = {
    "synthesis_mixing": "synthesis_mixing.json",
    "synthesis_powder_bead_cabin": "synthesis_powder_bead_cabin.json",
    "synthesis_powder_bead_rack2": "synthesis_powder_bead_rack2.json",
}


def load_legacy_workflow(name: str) -> dict[str, Any]:
    """Load one bundled legacy workflow export by its stable package name."""

    try:
        filename = WORKFLOW_FILES[name]
    except KeyError as exc:
        raise KeyError(f"unknown legacy workflow: {name}") from exc
    payload = files(__package__).joinpath(filename).read_text(encoding="utf-8")
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError(f"legacy workflow is not a JSON object: {filename}")
    return document


__all__ = ["WORKFLOW_FILES", "load_legacy_workflow"]
