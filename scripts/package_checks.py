#!/usr/bin/env python3
"""Checks shared by the YB package validation script and unit tests.

The functions in this module deliberately do not import the device package.  A
package check must be able to report a malformed package before any device
constructor (or hardware connection) is started.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SNAKE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

RUNTIME_PARTS = frozenset(
    {
        ".agents",
        ".git",
        ".pytest_cache",
        ".unilabos",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "build",
        "dist",
        ".eggs",
    }
)


def is_runtime_path(path: str | Path) -> bool:
    """Return whether *path* belongs to local runtime/development state."""

    return any(part in RUNTIME_PARTS for part in Path(path).parts)


def _error(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_workflow_manifest(
    package_root: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Validate package.yaml workflow entries and their source files."""

    errors: list[str] = []
    package_section = manifest.get("package")
    if not isinstance(package_section, dict) or not package_section.get("name"):
        _error(errors, "package.yaml must contain package.name")

    workflows = manifest.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        _error(errors, "package.yaml must contain a non-empty workflows list")
        return errors

    seen_uuid: set[str] = set()
    seen_source: set[str] = set()
    for index, item in enumerate(workflows):
        if not isinstance(item, dict):
            _error(errors, f"workflows[{index}] must be a mapping")
            continue
        workflow_uuid = str(item.get("workflow_uuid", ""))
        source = str(item.get("source", ""))
        if not UUID_RE.fullmatch(workflow_uuid):
            _error(errors, f"workflows[{index}] has invalid workflow_uuid: {workflow_uuid!r}")
        elif workflow_uuid in seen_uuid:
            _error(errors, f"duplicate workflow_uuid: {workflow_uuid}")
        seen_uuid.add(workflow_uuid)
        if not source:
            _error(errors, f"workflows[{index}] is missing source")
            continue
        if source in seen_source:
            _error(errors, f"duplicate workflow source: {source}")
        seen_source.add(source)
        source_path = package_root / source
        if not source_path.is_file():
            _error(errors, f"workflow source does not exist: {source}")
        if not source.startswith("yb_sse_devices/workflows/"):
            _error(
                errors,
                f"workflow source must live under yb_sse_devices/workflows/: {source}",
            )
    return errors


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _error(errors, f"invalid JSON {path}: {exc}")
        return None
    if not isinstance(data, dict):
        _error(errors, f"JSON root must be an object: {path}")
        return None
    return data


def validate_graphs(
    package_root: Path,
    catalog: dict[str, Any] | None = None,
) -> list[str]:
    """Validate node/link references and registered classes in deployment graphs."""

    errors: list[str] = []
    graph_dir = package_root / "deployment" / "graphs"
    if not graph_dir.is_dir():
        return [f"missing deployment graph directory: {graph_dir}"]

    registered_classes: set[str] = set()
    if catalog:
        for kind in ("devices", "resources"):
            for definition in catalog.get("definitions", {}).get(kind, []):
                if not isinstance(definition, dict):
                    continue
                fqid = definition.get("fqid")
                if isinstance(fqid, str):
                    registered_classes.add(fqid)

    for graph_path in sorted(graph_dir.glob("*.json")):
        graph = _load_json(graph_path, errors)
        if graph is None:
            continue
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            _error(errors, f"{graph_path}: nodes must be a list")
            continue
        node_ids: set[str] = set()
        for node_index, node in enumerate(nodes):
            if not isinstance(node, dict):
                _error(errors, f"{graph_path}: nodes[{node_index}] must be an object")
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                _error(errors, f"{graph_path}: nodes[{node_index}] missing id")
                continue
            if node_id in node_ids:
                _error(errors, f"{graph_path}: duplicate node id {node_id}")
            node_ids.add(node_id)
            parent = node.get("parent")
            if parent is not None and parent not in node_ids and not any(
                isinstance(candidate, dict) and candidate.get("id") == parent
                for candidate in nodes
            ):
                _error(errors, f"{graph_path}: {node_id} references missing parent {parent}")
            class_name = node.get("class")
            if registered_classes and class_name not in registered_classes:
                _error(errors, f"{graph_path}: unregistered class {class_name!r}")
            children = node.get("children", [])
            if not isinstance(children, list):
                _error(errors, f"{graph_path}: children for {node_id} must be a list")
            else:
                for child in children:
                    if child not in {candidate.get("id") for candidate in nodes if isinstance(candidate, dict)}:
                        _error(errors, f"{graph_path}: {node_id} references missing child {child}")

        links = graph.get("links", [])
        if not isinstance(links, list):
            _error(errors, f"{graph_path}: links must be a list")
        else:
            for link_index, link in enumerate(links):
                if not isinstance(link, dict):
                    _error(errors, f"{graph_path}: links[{link_index}] must be an object")
                    continue
                endpoints: Iterable[Any] = (
                    link.get("source"),
                    link.get("target"),
                    link.get("from"),
                    link.get("to"),
                )
                for endpoint in endpoints:
                    if endpoint is not None and endpoint not in node_ids:
                        _error(errors, f"{graph_path}: link references missing node {endpoint}")
    return errors


def validate_catalog_layout(
    package_root: Path,
    catalog: dict[str, Any],
) -> list[str]:
    """Check that catalog definitions live in the canonical package folders."""

    errors: list[str] = []
    definitions = catalog.get("definitions")
    if not isinstance(definitions, dict):
        return ["package catalog is missing definitions"]
    for kind, directory in (("devices", "yb_sse_devices/devices"), ("resources", "yb_sse_devices/resources"), ("workflows", "yb_sse_devices/workflows")):
        entries = definitions.get(kind, [])
        if not isinstance(entries, list):
            _error(errors, f"catalog definitions.{kind} must be a list")
            continue
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                _error(errors, f"catalog definitions.{kind} contains a non-object")
                continue
            identifier = str(entry.get("id", ""))
            if not SNAKE_ID_RE.fullmatch(identifier):
                _error(errors, f"{kind} has non-snake_case id: {identifier!r}")
            if identifier in seen:
                _error(errors, f"{kind} has duplicate id: {identifier}")
            seen.add(identifier)
            declaring_file = str(entry.get("declaring_file", ""))
            if not declaring_file.startswith(directory + "/"):
                _error(errors, f"{kind} definition is outside {directory}/: {declaring_file}")
            source_path = package_root / declaring_file
            if not source_path.is_file():
                _error(errors, f"{kind} declaring file does not exist: {declaring_file}")
    return errors


def validate_publications(
    package_root: Path,
    manifest: dict[str, Any],
) -> list[str]:
    """Ensure every declared workflow has a matching publication and contract."""

    errors: list[str] = []
    declared = {
        str(item.get("workflow_uuid"))
        for item in manifest.get("workflows", [])
        if isinstance(item, dict) and item.get("workflow_uuid")
    }
    publication_root = package_root / "yb_sse_devices" / "workflow_publications"
    published_dirs = {
        path.name for path in publication_root.iterdir() if path.is_dir()
    } if publication_root.is_dir() else set()
    for workflow_uuid in sorted(declared - published_dirs):
        _error(errors, f"missing workflow publication directory: {workflow_uuid}")

    index_path = package_root / "yb_sse_devices" / "workflow_publications.json"
    if not index_path.is_file():
        return errors + [f"missing workflow publication index: {index_path}"]
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return errors + [f"invalid workflow publication index: {exc}"]
    entries = index.get("publications") if isinstance(index, dict) else None
    if not isinstance(entries, list):
        return errors + ["workflow_publications.json must contain publications list"]

    for entry in entries:
        if not isinstance(entry, dict):
            _error(errors, "workflow publication entry must be an object")
            continue
        workflow_uuid = str(entry.get("workflow_uuid", ""))
        contract_uuid = str(entry.get("contract_uuid", ""))
        if workflow_uuid not in declared:
            _error(errors, f"publication references undeclared workflow: {workflow_uuid}")
            continue
        contract_path = publication_root / workflow_uuid / "contracts" / f"{contract_uuid}.json"
        if not contract_path.is_file():
            _error(errors, f"publication contract file does not exist: {contract_path}")

    for workflow_uuid in sorted(declared & published_dirs):
        manifest_path = publication_root / workflow_uuid / "manifest.json"
        contract_dir = publication_root / workflow_uuid / "contracts"
        if not manifest_path.is_file():
            _error(errors, f"publication missing manifest: {manifest_path}")
            continue
        try:
            publication = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _error(errors, f"invalid publication manifest {manifest_path}: {exc}")
            continue
        if publication.get("workflow_uuid") != workflow_uuid:
            _error(errors, f"publication manifest UUID mismatch: {manifest_path}")
        contract_uuids = publication.get("contract_uuids", [])
        if not isinstance(contract_uuids, list) or not contract_uuids:
            _error(errors, f"publication has no contract UUIDs: {manifest_path}")
            continue
        latest_contract_uuid = publication.get("latest_contract_uuid")
        if latest_contract_uuid not in contract_uuids:
            _error(
                errors,
                f"publication latest_contract_uuid is not listed in contract_uuids: {manifest_path}",
            )
        for contract_uuid in contract_uuids:
            contract_path = contract_dir / f"{contract_uuid}.json"
            if not contract_path.is_file():
                _error(errors, f"publication manifest references missing contract: {contract_path}")
    return errors


def absolute_path_findings(package_root: Path) -> list[str]:
    """Find machine-local paths in runtime configuration and protocol data."""

    findings: list[str] = []
    candidate_extensions = {".json", ".yaml", ".yml", ".toml", ".py"}
    for path in package_root.rglob("*"):
        if not path.is_file() or is_runtime_path(path.relative_to(package_root)):
            continue
        if path.relative_to(package_root).parts[:1] in (("scripts",), ("tests",)):
            continue
        if path.suffix.lower() not in candidate_extensions:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"(?:/Users/|/home/|[A-Za-z]:\\\\)", content):
            findings.append(str(path.relative_to(package_root)))
    return findings


def empty_action_results(catalog: dict[str, Any]) -> list[str]:
    """Return action identifiers whose generated result schema has no fields."""

    findings: list[str] = []
    for device in catalog.get("definitions", {}).get("devices", []):
        if not isinstance(device, dict):
            continue
        fqid = device.get("fqid", device.get("id", "<device>"))
        mappings = (
            device.get("details", {})
            .get("registry_entry", {})
            .get("class", {})
            .get("action_value_mappings", {})
        )
        if not isinstance(mappings, dict):
            continue
        for action_name, action in mappings.items():
            if not isinstance(action, dict):
                continue
            schema = action.get("schema", {})
            result = schema.get("properties", {}).get("result", {}) if isinstance(schema, dict) else {}
            properties = result.get("properties", {}) if isinstance(result, dict) else {}
            if not properties:
                findings.append(f"{fqid}.{action_name}")
    return findings


def run_checks(
    package_root: Path,
    catalog_path: Path,
    *,
    strict_actions: bool = False,
) -> list[str]:
    """Run all source-tree checks and return human-readable errors."""

    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - only broken environments
        return [f"PyYAML is required for package checks: {exc}"]

    errors: list[str] = []
    try:
        manifest = yaml.safe_load((package_root / "package.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"invalid package.yaml: {exc}"]
    if not isinstance(manifest, dict):
        return ["package.yaml root must be a mapping"]
    errors.extend(validate_workflow_manifest(package_root, manifest))

    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"invalid package catalog {catalog_path}: {exc}"]
    if not isinstance(catalog, dict):
        return [f"package catalog root must be an object: {catalog_path}"]

    errors.extend(validate_graphs(package_root, catalog))
    errors.extend(validate_catalog_layout(package_root, catalog))
    errors.extend(validate_publications(package_root, manifest))
    for path in absolute_path_findings(package_root):
        errors.append(f"machine-local absolute path found in {path}")
    if strict_actions:
        for action in empty_action_results(catalog):
            errors.append(f"action has empty result schema: {action}")
    return errors


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--strict-actions", action="store_true")
    args = parser.parse_args()
    failures = run_checks(args.root.resolve(), args.catalog.resolve(), strict_actions=args.strict_actions)
    if failures:
        for failure in failures:
            print(f"[package-check] ERROR: {failure}", file=sys.stderr)
        raise SystemExit(1)
    print("[package-check] manifest, graphs, publications and release paths are valid")
