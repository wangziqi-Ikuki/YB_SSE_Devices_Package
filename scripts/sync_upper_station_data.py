"""Synchronize recipe data from the legacy YB upper computer.

The legacy application keeps these files beside its executable.  The device
package uses the copied recipe snapshot when the upper application is not
installed, and can be pointed at the live directory with
YB_SYNTHESIS_RECIPE_DIR for a controlled workstation deployment.  The rack
inventory is installation state, so copying it is explicit: use --sync-rack
only after the physical rack contents have been verified.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("station_dir", type=Path, help="legacy upper-computer directory")
    parser.add_argument(
        "--package-data",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "yb_sse_devices" / "data",
    )
    parser.add_argument(
        "--sync-rack",
        action="store_true",
        help="also replace the package rack snapshot from rack/rackdata.json",
    )
    args = parser.parse_args()

    source_recipes = args.station_dir / "recipes"
    source_rack = args.station_dir / "rack" / "rackdata.json"
    if not source_recipes.is_dir():
        parser.error(f"recipe directory not found: {source_recipes}")
    if args.sync_rack and not source_rack.is_file():
        parser.error(f"rack inventory not found: {source_rack}")

    target_recipes = args.package_data / "recipes"
    target_recipes.mkdir(parents=True, exist_ok=True)
    for path in target_recipes.glob("*.json"):
        path.unlink()
    copied = 0
    for path in sorted(source_recipes.glob("*.json")):
        shutil.copy2(path, target_recipes / path.name)
        copied += 1
    args.package_data.mkdir(parents=True, exist_ok=True)
    if args.sync_rack:
        shutil.copy2(source_rack, args.package_data / "rackdata.json")
        print(f"copied {copied} recipes and rackdata.json into {args.package_data}")
    else:
        print(f"copied {copied} recipes; kept the package rack snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
