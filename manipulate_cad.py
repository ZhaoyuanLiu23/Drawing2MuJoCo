"""Independent Panda manipulation entry point; run in the MuJoCo environment."""
import argparse
from pathlib import Path

from manipulation.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="Exterior-edge CAD rigid-body grasp and lift")
    parser.add_argument("scene", type=Path, help="CAD2MuJoCo scene.xml containing Panda and the target")
    parser.add_argument("--object", default="cad_part")
    parser.add_argument("--table", default="cad_test_table")
    parser.add_argument("--output", type=Path, default=Path("outputs/manipulation"))
    parser.add_argument("--pose-source", choices=("ground_truth", "vision"), default="ground_truth")
    args = parser.parse_args()
    try:
        runner = run_pipeline
        if args.pose_source == "vision":
            from perception.pipeline import run_pipeline as runner
        result = runner(args.scene, args.output, object_name=args.object, table_name=args.table)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print("Grasp:", "PASS" if result["success"] else "FAIL")
    print("Checks:", result["checks"])
    print("Failure:", result["failure"])
    print("Results:", args.output.resolve())
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
