#!/usr/bin/env bash
set -euo pipefail

# Validate the source tree and generate a temporary Uni-Lab package catalog.
# This script never writes runtime state into the repository. Set
# YB_KEEP_CHECK_OUTPUT=1 to keep the generated report in YB_CHECK_OUT.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_PROJECT="${UNILAB_OS_PROJECT:-/Users/dp/Desktop/0918YB/Uni-Lab-OS}"
PYTHON_ENV="${UNILAB_PYTHON_ENV:-unilab}"
if [[ -n "${YB_CHECK_OUT:-}" ]]; then
  CHECK_OUT="$YB_CHECK_OUT"
else
  CHECK_OUT="$(mktemp -d "${TMPDIR:-/tmp}/yb-package-check.XXXXXX")"
fi

if command -v mamba >/dev/null 2>&1; then
  PYTHON=(mamba run -n "$PYTHON_ENV" python)
else
  PYTHON=(python3)
fi

export PYTHONPATH="$OS_PROJECT${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -f "$ROOT_DIR/package.yaml" || ! -f "$ROOT_DIR/pyproject.toml" ]]; then
  echo "[package-check] package.yaml and pyproject.toml are required" >&2
  exit 2
fi

mkdir -p "$CHECK_OUT"

echo "[package-check] compiling Python sources"
"${PYTHON[@]}" -m compileall -q "$ROOT_DIR/yb_sse_devices"

echo "[package-check] checking canonical package directories"
for required_dir in \
  "$ROOT_DIR/yb_sse_devices/devices" \
  "$ROOT_DIR/yb_sse_devices/resources" \
  "$ROOT_DIR/yb_sse_devices/workflows"; do
  if [[ ! -d "$required_dir" ]]; then
    echo "[package-check] missing canonical directory: ${required_dir#$ROOT_DIR/}" >&2
    exit 1
  fi
done

echo "[package-check] inspecting package catalog"
"${PYTHON[@]}" -m unilabos package inspect \
  --path "$ROOT_DIR" \
  --out "$CHECK_OUT/inspect" \
  >"$CHECK_OUT/inspect.log"
cat "$CHECK_OUT/inspect.log"

CHECK_ARGS=(
  --root "$ROOT_DIR"
  --catalog "$CHECK_OUT/inspect/package.catalog.json"
)
if [[ -n "${YB_STRICT_ACTION_RESULTS:-}" ]]; then
  CHECK_ARGS+=(--strict-actions)
fi
"${PYTHON[@]}" "$ROOT_DIR/scripts/package_checks.py" "${CHECK_ARGS[@]}"

if [[ "${YB_KEEP_CHECK_OUTPUT:-0}" != "1" ]]; then
  rm -rf "$CHECK_OUT"
else
  echo "[package-check] reports retained at $CHECK_OUT"
fi
