#!/usr/bin/env bash
set -euo pipefail

# Build a clean, catalog-embedded package artifact.  package inspect currently
# archives the complete workspace, so we stage a filtered copy first.  This
# keeps local .agents/.unilabos/logs and Python caches out of deliverables while
# leaving those directories untouched in the developer's checkout.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_PROJECT="${UNILAB_OS_PROJECT:-/Users/dp/Desktop/0918YB/Uni-Lab-OS}"
PYTHON_ENV="${UNILAB_PYTHON_ENV:-unilab}"
ARTIFACT_DIR="${1:-$ROOT_DIR/dist/package}"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yb-package-stage.XXXXXX")"
BUILD_OUT="$(mktemp -d "${TMPDIR:-/tmp}/yb-package-build.XXXXXX")"

cleanup() {
  rm -rf "$STAGE_DIR" "$BUILD_OUT"
}
trap cleanup EXIT

if command -v mamba >/dev/null 2>&1; then
  PYTHON=(mamba run -n "$PYTHON_ENV" python)
else
  PYTHON=(python3)
fi
export PYTHONPATH="$OS_PROJECT${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p "$ARTIFACT_DIR"

echo "[package-build] staging clean source tree"
mkdir -p "$STAGE_DIR/package"
rsync -a \
  --exclude '.git/' \
  --exclude '.agents/' \
  --exclude '.unilabos/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.py[cod]' \
  --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.tox/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude '*.egg-info/' \
  --exclude '.DS_Store' \
  "$ROOT_DIR/" "$STAGE_DIR/package/"

echo "[package-build] validating staged source"
YB_CHECK_OUT="$BUILD_OUT/check" \
YB_KEEP_CHECK_OUTPUT=1 \
  "$STAGE_DIR/package/scripts/check-package.sh"

echo "[package-build] creating catalog-embedded wheel"
"${PYTHON[@]}" -m unilabos package build \
  --path "$STAGE_DIR/package" \
  --out "$BUILD_OUT/artifacts" \
  | tee "$BUILD_OUT/build.log"

cp -f "$BUILD_OUT/artifacts"/* "$ARTIFACT_DIR/"

echo "[package-build] auditing archive contents"
"${PYTHON[@]}" - "$ARTIFACT_DIR" <<'PY'
from __future__ import annotations

import sys
import zipfile
from pathlib import Path
import tarfile

out = Path(sys.argv[1])
for archive in sorted(out.glob("*.whl")):
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        forbidden = [
            name for name in names
            if any(part in {".agents", ".unilabos", "__pycache__", ".pytest_cache", ".git"}
                   for part in Path(name).parts)
        ]
        if forbidden:
            raise SystemExit(f"forbidden runtime files in {archive}: {forbidden[:5]}")
        if not any(name.endswith("yb_synthesis_modbus.yaml") for name in names):
            raise SystemExit(f"protocol YAML is missing from {archive}")
    print(f"[package-build] wheel OK: {archive}")

for archive in sorted(out.glob("*.tar.gz")):
    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
        forbidden = [
            name for name in names
            if any(part in {".agents", ".unilabos", "__pycache__", ".pytest_cache", ".git"}
                   for part in Path(name).parts)
        ]
        if forbidden:
            raise SystemExit(f"forbidden runtime files in {archive}: {forbidden[:5]}")
    print(f"[package-build] source archive OK: {archive}")

if not list(out.glob("*.whl")):
    raise SystemExit("package build produced no wheel")
PY

echo "[package-build] artifacts written to $ARTIFACT_DIR"
find "$ARTIFACT_DIR" -maxdepth 1 -type f -print | sort
