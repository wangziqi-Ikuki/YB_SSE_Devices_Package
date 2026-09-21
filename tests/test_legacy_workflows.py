"""Validate and exercise the bundled legacy synthesis workflow exports."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from importlib.resources import files
from typing import Any

import pytest

from yb_sse_devices.devices.synthesis_station.device import SynthesisStation
from yb_sse_devices.devices.yb_synthesis_atomic_station.device import (
    YBSynthesisAtomicStation,
)
from yb_sse_devices.legacy_workflows import WORKFLOW_FILES, load_legacy_workflow


EXPECTED: dict[str, dict[str, Any]] = {
    "synthesis_powder_bead_cabin": {
        "workflow_name": "合成工站 加粉加珠流程 方舱",
        "workflow_uuid": "00cdaee3-7c63-4c77-875d-3d7093d0e5e3",
        "sha256": "7f2eb6e822e53d48d98124aecb793a73dea4d0a354e1c1f330c95a2f0a8a70ba",
        "node_count": 5,
        "edge_count": 6,
        "manual_count": 2,
        "actions": ["upload_cubic", "close_cabin_outer_door", "start_recipt"],
    },
    "synthesis_powder_bead_rack2": {
        "workflow_name": "合成工站 加粉加珠流程 料架2",
        "workflow_uuid": "2b618ad2-cb04-4ce0-8f0f-4155497740b2",
        "sha256": "f8e7283ab6d8906f1d61c46514a0aebc593f794e654d78206774fe7bda32ae70",
        "node_count": 3,
        "edge_count": 3,
        "manual_count": 1,
        "actions": ["upload_cubic", "start_recipt"],
    },
    "synthesis_mixing": {
        "workflow_name": "合成工站混料流程",
        "workflow_uuid": "bbf54bc0-4119-48be-a7b7-29d84a6a45a9",
        "sha256": "84db137a50d9b91e96cd444fd0237a2822708e4fd2ba21d329aec9f3ac8fc6ba",
        "node_count": 8,
        "edge_count": 12,
        "manual_count": 2,
        "actions": [
            "upload_cubic",
            "close_cabin_outer_door",
            "start_recipt",
            "start_acoustic_resonance",
            "fetch_acoustic_resonance",
            "finish_acoustic_resonance",
        ],
    },
}


def _ordered_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return nodes in dependency order, collapsing duplicate dependency edges."""

    data = document["data"]
    nodes = data["nodes"]
    by_uuid = {node["uuid"]: node for node in nodes}
    order = {node["uuid"]: index for index, node in enumerate(nodes)}
    successors: dict[str, set[str]] = defaultdict(set)
    indegree = {node_uuid: 0 for node_uuid in by_uuid}
    for edge in data["edges"]:
        source = edge["source_node_uuid"]
        target = edge["target_node_uuid"]
        assert source in by_uuid
        assert target in by_uuid
        assert edge["source_handle_io"] == "source"
        assert edge["target_handle_io"] == "target"
        if target not in successors[source]:
            successors[source].add(target)
            indegree[target] += 1

    ready = sorted(
        (node_uuid for node_uuid, count in indegree.items() if count == 0),
        key=order.__getitem__,
    )
    result: list[dict[str, Any]] = []
    while ready:
        node_uuid = ready.pop(0)
        result.append(by_uuid[node_uuid])
        for target in sorted(successors[node_uuid], key=order.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=order.__getitem__)
    assert len(result) == len(nodes), "legacy workflow contains a dependency cycle"
    return result


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_legacy_workflow_asset_integrity(name: str) -> None:
    expected = EXPECTED[name]
    filename = WORKFLOW_FILES[name]
    resource = files("yb_sse_devices.legacy_workflows").joinpath(filename)
    payload = resource.read_bytes()
    document = load_legacy_workflow(name)
    data = document["data"]

    assert hashlib.sha256(payload).hexdigest() == expected["sha256"]
    assert document["name"] == expected["workflow_name"]
    assert data["workflow_name"] == expected["workflow_name"]
    assert data["workflow_uuid"] == expected["workflow_uuid"]
    assert len(data["nodes"]) == expected["node_count"]
    assert len(data["edges"]) == expected["edge_count"]

    ordered = _ordered_nodes(document)
    manual_nodes = [node for node in ordered if node["type"] == "manual_confirm"]
    action_nodes = [node for node in ordered if node["type"] == "ILab"]
    assert len(manual_nodes) == expected["manual_count"]
    assert all(node["param"]["timeout_seconds"] == 3600 for node in manual_nodes)
    assert [node["name"] for node in action_nodes] == expected["actions"]
    assert all(hasattr(SynthesisStation, node["name"]) for node in action_nodes)
    assert all(
        hasattr(YBSynthesisAtomicStation, node["name"]) for node in action_nodes
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_legacy_workflow_actions_run_in_dependency_order(name: str) -> None:
    """Run each legacy action chain against an isolated in-process station mock."""

    station = SynthesisStation(
        config={
            "ip": "127.0.0.1",
            "port": 0,
            "use_mock": True,
            "status_poll_interval": 0,
            "response_timeout": 2.0,
        }
    )
    recipe_name = f"legacy-{name}"
    try:
        assert station.upload_recipe(
            recipe_name=recipe_name,
            formula="Li6PS5Cl",
            synthesis_mass=3.86,
            n_ball_bead=80,
        )["result"] == 0
        created = station.create_task(
            pallet_type=1,
            cubic_type=1,
            task_slot_nums=[1],
            task_recipe_names=[recipe_name],
            has_bead_bottle=True,
            bead_count=80,
        )
        task_id = created["task_id"]
        assert station.start_task(task_id)["task_id"] == task_id

        for node in _ordered_nodes(load_legacy_workflow(name)):
            if node["type"] == "manual_confirm":
                # The original approval gate remains in the JSON. The isolated
                # simulator grants it so that the following device action runs.
                continue
            params = dict(node.get("param") or {})
            if "task_id" in params:
                params["task_id"] = task_id
            result = getattr(station, node["name"])(**params)
            assert result["result"] == 0, (node["name"], result)
            assert result.get("task_id", task_id) == task_id
    finally:
        station.close()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_converted_legacy_actions_run_on_atomic_station(name: str) -> None:
    """Execute each converted chain through the current single-device facade."""

    document = load_legacy_workflow(name)
    first_action = next(
        node for node in _ordered_nodes(document) if node["type"] == "ILab"
    )
    fetch_source = int(first_action["param"].get("fetch_cubic_source", 1))
    station = YBSynthesisAtomicStation(
        config={
            "simulation": True,
            "business_simulation": True,
            "simulation_handshake_delay": 0.01,
            "simulation_dosing_duration": 0.01,
        },
        action_timeout=2.0,
        simulation_step=0.01,
    )
    try:
        created = station.create_batch(
            recipe_name=f"atomic-legacy-{name}",
            task_slot_nums=[1],
            fetch_cubic_source=fetch_source,
        )
        task_id = created["task_id"]
        assert task_id

        for node in _ordered_nodes(document):
            if node["type"] == "manual_confirm":
                continue
            params = dict(node.get("param") or {})
            params["task_id"] = task_id
            result = getattr(station, node["name"])(**params)
            assert result["task_id"] == task_id
            assert result["state"].startswith("LEGACY_")
    finally:
        station.close()
