"""Web-only orchestration of existing CAD/robot APIs, in the MuJoCo interpreter.

No live object state is written. Start pose is authored in a new MJCF, before
any task instance exists. Task success remains the original visual verdict.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.simulation_request import validate_parameters, result_template


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")


def preflight(cad, output, parameters, profile):
    """Compose an unstepped calibration scene, reuse the existing table guard.

CAD2MuJoCo.convert performs drop physics, so the geometric guard must precede
that call. All mesh, inertia, scene and boundary calculations use existing APIs.
"""
    import numpy as np
    from cad_mujoco.mesh import read_stl, mass_properties, write_stl
    from cad_mujoco.metadata import mesh_unit, UNIT_TO_M, verify_pair
    from cad_mujoco.mjcf import part_xml, make_drop_scene, save_spec
    from manipulation.geometry import Pose, quat_matrix
    from pick_place_task.planning import world_bounds, TargetZone, inside_zone, TaskConfig
    from pick_place_task.scene import add_target_zone

    parsed = json.loads((cad / "parsed.json").read_text(encoding="utf8"))
    unit, _, _ = mesh_unit(parsed, None)
    triangles = read_stl(cad / "model.stl") * UNIT_TO_M[unit]
    properties = mass_properties(triangles, density_kg_m3=profile["density_kg_m3"])
    verify_pair(parsed, triangles, properties)
    triangles -= np.asarray(properties["com_m"])
    properties["com_m"] = [0., 0., 0.]
    bounds = [triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))]
    output.mkdir()
    write_stl(output / "meshes/visual.stl", triangles)
    (output / "part.xml").write_text(part_xml(properties), encoding="utf8")
    spec, info = make_drop_scene(output / "part.xml", bounds, base_scene=profile["panda_scene"])
    source = output / "scene.xml"
    save_spec(spec, source)
    xy = [parameters["target"]["x"], parameters["target"]["y"]]
    zone = add_target_zone(source, output / "target_check.xml", xy, profile["zone_size_m"], "cad_test_table")
    angle = np.deg2rad(parameters["object_start"]["yaw_deg"] % 360)
    quat = [float(np.cos(angle / 2)), 0., 0., float(np.sin(angle / 2))]
    start = parameters["object_start"]
    footprint = world_bounds(triangles.reshape(-1, 3), Pose(np.array([start["x"], start["y"], 0.]), quat_matrix(quat)))
    # Reuse exactly the target-zone table containment check for the rotated
    # object's enclosing rectangle too; no screen-position/part-specific bounds.
    try:
        add_target_zone(source, output / "start_check.xml", footprint.mean(0)[:2],
                        (footprint[1] - footprint[0])[:2], "cad_test_table")
    except ValueError as exc:
        raise ValueError("object_start_outside_table: " + str(exc)) from exc
    expected_bounds = footprint.copy()
    expected_bounds[:, :2] += np.asarray(xy) - footprint.mean(0)[:2]
    if not inside_zone(expected_bounds, TargetZone(xy, profile["zone_size_m"], zone.table_top), TaskConfig().zone_margin_m):
        raise ValueError("target_zone_too_small_for_requested_footprint")
    return dict(quaternion_wxyz=quat, initial_z_m=info["initial_com_world_m"][2],
                table_top_m=info["table_top_m"], table_half_size_m=info["table_half_size_m"],
                start_envelope_m=footprint.tolist(), source="existing make_drop_scene + add_target_zone + inside_zone; no mj_step")


def configure_start(source, destination, parameters, evidence):
    from cad_mujoco.mjcf import load_spec, save_spec
    spec = load_spec(source)
    start = parameters["object_start"]
    body = spec.body("cad_part")
    body.pos = [start["x"], start["y"], evidence["initial_z_m"]]
    body.quat = evidence["quaternion_wxyz"]
    model = spec.compile()
    adr = int(model.joint("cad_free").qposadr[0])
    # Initial MJCF keyframes only, following CAD2MuJoCo's attach_part contract.
    # Never writes MjData qpos/qvel/xpos or edits state after simulation begins.
    for key in spec.keys:
        initial = list(key.qpos)
        initial[adr:adr + 7] = model.qpos0[adr:adr + 7]
        key.qpos = initial
    save_spec(spec, destination)


def execute(run_dir, *, task_factory=None):
    run_dir = Path(run_dir).resolve()
    request = json.loads((run_dir / "request.json").read_text(encoding="utf8"))
    parameters = validate_parameters(request["parameters"])
    job = run_dir.parents[2]
    cad = job / "cad"
    result = result_template(job.name, run_dir.name)
    result.update(parameters=parameters, profile=request["profile"], physics_started=False,
                  success_source="unchanged Agent + fresh post-release RGB-D visual verification",
                  pose_source="vision", viewer=False)
    stage = "preflight"
    recordings = []
    try:
        evidence = preflight(cad, run_dir / "preflight", parameters, request["profile"])
        result["preflight"] = evidence
        write_json(run_dir / "preflight.json", evidence)
        stage = "cad2mujoco"
        from cad_mujoco.pipeline import convert
        result["physics_started"] = True
        manifest, drop = convert(cad / "model.stl", cad / "parsed.json", run_dir / "cad2mujoco",
                                 density_kg_m3=request["profile"]["density_kg_m3"],
                                 base_scene=request["profile"]["panda_scene"])
        result["cad_drop_success"] = drop["success"]
        if not drop["success"]:
            result["error"] = dict(code="CAD_DROP_FAILED", message="CAD2MuJoCo 落体验证失败。", stage=stage, checks=drop["checks"])
        else:
            configure_start(run_dir / "cad2mujoco/scene.xml", run_dir / "scene.xml", parameters, evidence)
            stage = "agent"
            from embodied_agent.models import TaskPlan
            from embodied_agent.runner import run_agent
            from embodied_agent.backend import gated_task_type
            from cad_mujoco.mjcf import load_spec, save_spec
            from simulation_video import SimulationVideo

            # Persist the exact scene with the target marker and RGB-D camera.
            # No override of run(), control, perception or success algorithms.
            class ArchivedTask(task_factory or gated_task_type()):
                def __init__(self, *args, **kwargs):
                    self.web_video = None
                    super().__init__(*args, **kwargs)
                    try:
                        save_spec(load_spec(self.scene_path), run_dir / "task_scene.xml")
                    except Exception:
                        self.close()
                        raise
                    self.web_video = SimulationVideo(self, run_dir)
                    recordings.append(self.web_video)
                    previous_observer = self.step_observer

                    def observe_step():
                        if previous_observer is not None:
                            previous_observer()
                        self.web_video.sample()

                    self.step_observer = observe_step

                def close(self):
                    # Same execution thread owns both GL contexts. No extra task.
                    try:
                        if self.web_video is not None:
                            self.web_video.close()
                    finally:
                        super().close()

            target = parameters["target"]
            plan = TaskPlan.build(dict(type="pick_and_place", object="cad_part", target=dict(
                center_xy_m=[target["x"], target["y"]], size_xy_m=request["profile"]["zone_size_m"], frame="world", unit="m")))
            plan = run_agent(run_dir / "scene.xml", plan, run_dir / "agent", viewer=False, task_factory=ArchivedTask)
            result["steps"] = [dict(skill=s["skill"], status=s["status"], error=(s["result"] or {}).get("error")) for s in plan.steps]
            pipeline_path = run_dir / "agent/skills/pipeline_result.json"
            pipeline = json.loads(pipeline_path.read_text(encoding="utf8")) if pipeline_path.is_file() else {}
            for key in ("perception_success", "grasp_execution_completed", "visual_task_success"):
                result[key] = pipeline.get(key) is True
            result["place_execution_completed"] = plan.steps[3]["status"] == "succeeded"
            verification = pipeline.get("visual_verification") or {}
            result["visual_verification"] = verification
            if verification.get("center_error_m") is not None:
                result["position_error_mm"] = 1000 * verification["center_error_m"]
            if plan.status == "succeeded" and result["visual_task_success"] and verification.get("success") is True:
                result["status"] = "succeeded"
            else:
                failure = plan.failures[0]["error"] if plan.failures else dict(code="VISUAL_VERIFY_FAILED", message="未获得完整视觉成功证据。")
                result["error"] = failure
    except Exception as exc:
        result["status"] = "rejected" if stage == "preflight" else "failed"
        result["error"] = dict(code="INVALID_SCENE_PARAMETERS" if stage == "preflight" else "SIMULATION_FAILED", message=str(exc), stage=stage)
    if recordings:
        result["video"] = recordings[0].info
        result["video_available"] = result["video"]["available"]
        if result["video_available"]:
            result["video_url"] = f"/api/jobs/{job.name}/simulation/{run_dir.name}/simulation.mp4"
    write_json(run_dir / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = execute(args.run_dir.resolve())
    print(result["status"], result["error"], flush=True)
    raise SystemExit(0 if result["status"] == "succeeded" else 1)
