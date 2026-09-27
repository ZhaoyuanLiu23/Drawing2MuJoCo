"""Independent visual pick-and-place task; all CLI lengths are world metres."""
import argparse
from pathlib import Path

from pick_place_task.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="Vision CAD pick-and-place and post-release visual verification")
    parser.add_argument("scene", type=Path)
    parser.add_argument("--target-xy", type=float, nargs=2, required=True, metavar=("X", "Y"), help="Target center in world metres")
    parser.add_argument("--zone-size", type=float, nargs=2, required=True, metavar=("WIDTH", "HEIGHT"), help="Target-zone XY extent in metres")
    parser.add_argument("--object", default="cad_part")
    parser.add_argument("--table", default="cad_test_table")
    parser.add_argument("--output", type=Path, default=Path("outputs/pick_place"))
    args = parser.parse_args()
    try:
        result = run_pipeline(args.scene, args.output, target_xy=args.target_xy, zone_size=args.zone_size,
                              object_name=args.object, table_name=args.table)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")
    print("Visual pick-place:", "PASS" if result["success"] else "FAIL")
    print("Checks:", result["checks"])
    print("Failure:", result["failure"])
    print("Results:", args.output.resolve())
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
