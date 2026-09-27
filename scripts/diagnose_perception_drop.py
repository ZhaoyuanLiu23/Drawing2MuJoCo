"""Reproduce fixture drop verdicts; diagnostic variants never change production.

All variants use the SAME validate_drop thresholds. Scene changes are saved only
inside the requested evidence directory. They are not used by perception tests.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/perception"))

import mujoco
import numpy as np
from test_perception import box
from cad_mujoco.mesh import write_stl
from cad_mujoco.pipeline import convert
from cad_mujoco.mjcf import load_spec, save_spec
from cad_mujoco.validation import initialize, validate_drop


def run(output, panda):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "diagnostic only; not a drop acceptance waiver", "mujoco_version": mujoco.__version__, "fixtures": []}
    for name, size, mass in (("small", [.091, .034, .008], .075), ("large", [.123, .052, .011], .14)):
        root = output / name
        root.mkdir(exist_ok=True)
        triangles = box(size)
        write_stl(root / "input.stl", triangles)
        (root / "input.json").write_text(json.dumps(dict(status="generated", units={"cad": "m"}, recipe={"unit": "m"})))
        _, baseline = convert(root / "input.stl", root / "input.json", root / "baseline", mass_kg=mass,
                              base_scene=panda, duration=3.)
        scene = root / "baseline/scene.xml"
        result = {"fixture": name, "size_m": size, "mass_kg": mass, "variants": {"baseline_3s": baseline}}
        for variant in ("baseline_8s", "half_timestep", "native_ccd", "box_contact", "soft_contact"):
            spec = load_spec(scene)
            duration = 3.
            if variant == "baseline_8s":
                duration = 8.
            elif variant == "half_timestep":
                spec.option.timestep /= 2  # Keep physical contact stiffness unchanged.
            elif variant == "native_ccd":
                spec.option.disableflags &= ~int(mujoco.mjtDisableBit.mjDSBL_NATIVECCD)
            elif variant == "box_contact":
                geom = spec.geom("cad_collision")
                geom.type = mujoco.mjtGeom.mjGEOM_BOX
                geom.meshname = ""
                geom.size = np.array(size) / 2
            elif variant == "soft_contact":
                spec.geom("cad_collision").solref = [.01, 1]
            target = root / variant / "scene.xml"
            target.parent.mkdir(exist_ok=True)
            save_spec(spec, target)
            check = validate_drop(target, triangles, [.5, 0, .13], duration=duration)
            result["variants"][variant] = check
            print(name, variant, check["success"], check["tail_max_linear_speed_m_s"], flush=True)
        model = load_spec(scene).compile()
        data = initialize(model)
        dof = int(model.joint("cad_free").dofadr[0])
        body = model.body("cad_part").id
        collision, table = model.geom("cad_collision").id, model.geom("cad_test_table").id
        trace = []
        while data.time < 3.:
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)
            if data.time >= 2.5:
                contacts = [c for c in data.contact if {c.geom1, c.geom2} == {collision, table}]
                trace.append([data.time, *data.xpos[body], *data.qvel[dof:dof+6], len(contacts),
                              min((c.dist for c in contacts), default=0.)])
        np.save(root / "baseline_tail_every_step.npy", np.asarray(trace))
        values = np.asarray(trace)
        result["baseline_tail_diagnostics"] = dict(columns=["time", "x", "y", "z", "vx", "vy", "vz", "wx", "wy", "wz", "contact_count", "min_contact_distance"],
            position_range_m=np.ptp(values[:, 1:4], axis=0).tolist(),
            velocity_min_m_s=values[:, 4:7].min(0).tolist(), velocity_max_m_s=values[:, 4:7].max(0).tolist(),
            contact_counts=np.unique(values[:, 10], return_counts=True)[0].tolist())
        report["fixtures"].append(result)
        (output / "diagnosis.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--panda-scene", required=True)
    args = parser.parse_args()
    run(args.output, args.panda_scene)
