import argparse
from pathlib import Path

from .pipeline import convert


def main(argv=None):
    parser = argparse.ArgumentParser(description="STL + parsed.json -> SI rigid part MJCF and validated drop scene")
    parser.add_argument("stl", type=Path)
    parser.add_argument("parsed", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/cad2mujoco"))
    mass = parser.add_mutually_exclusive_group(required=True)
    mass.add_argument("--density-kg-m3", type=float, help="Explicit uniform density; material is never guessed")
    mass.add_argument("--mass-kg", type=float, help="Explicit total mass; inertia assumes uniform density")
    parser.add_argument("--stl-unit", choices=("mm", "cm", "m", "in"))
    parser.add_argument("--source-up-axis", choices=("x", "y", "z"), default="z")
    parser.add_argument("--panda-scene", type=Path, help="Existing scene.xml; copied into a new independent test scene")
    parser.add_argument("--table-top-m", type=float, nargs=3, default=(0.5, 0, 0.13), metavar=("X", "Y", "Z"))
    parser.add_argument("--clearance-m", type=float, default=0.15)
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        manifest, result = convert(args.stl, args.parsed, args.output,
                                   density_kg_m3=args.density_kg_m3, mass_kg=args.mass_kg,
                                   stl_unit=args.stl_unit, source_up_axis=args.source_up_axis,
                                   base_scene=args.panda_scene, table_top_m=args.table_top_m,
                                   clearance_m=args.clearance_m, duration=args.duration)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, "CAD->MuJoCo failed: " + str(exc) + "\n")
    print("Output:", args.output.resolve())
    print("Status:", manifest["status"], "mass_kg:", manifest["rigid_body"]["mass_kg"])
    print("Collision: convex hull; openings filled for contact, retained in visual/mass properties")
    print("Drop:", "PASS" if result["success"] else "FAIL", result["checks"])
    return 0 if result["success"] else 1
