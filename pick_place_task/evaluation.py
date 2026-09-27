"""Read archived target state only AFTER the task verdict has been fixed."""
import numpy as np

from perception.evaluation import pose_error, state_pose
from .planning import inside_zone, world_bounds


def evaluate_offline(task):
    if not task.attempt_finished:
        raise RuntimeError("Ground truth evaluation is forbidden during task execution")
    adr = int(task.model.jnt_qposadr[task.object_joint])
    errors = []
    for (label, estimate), state in zip(task.observations, task.capture_states):
        errors.append(dict(label=label, **pose_error(estimate.pose, state_pose(state, adr), task.triangles)))
    tail = [(r, q) for r, q in zip(task.records, task.trajectory) if r["stage"] == "visual_verify"]
    poses = [state_pose(q, adr) for _, q in tail]
    bounds = np.array([world_bounds(task.vertices, pose) for pose in poses])
    result = dict(evaluation_only=True, used_for_task_success=False, pose_errors=errors)
    if len(bounds):
        centers = bounds.mean(1)
        times = np.array([r["time_s"] for r, _ in tail])
        result.update(final_gt_pose=poses[-1].json(), final_center_error_m=float(np.linalg.norm(centers[-1, :2] - task.zone.xy)),
                      retained_inside_zone=all(inside_zone(b, task.zone) for b in bounds),
                      minimum_bottom_z_m=float(bounds[:, 0, 2].min()), maximum_bottom_z_m=float(bounds[:, 0, 2].max()),
                      maximum_center_drift_m=float(np.max(np.linalg.norm(centers - centers[0], axis=1))),
                      verification_duration_s=float(times[-1] - times[0]))
    lift = [world_bounds(task.vertices, state_pose(q, adr))[0, 2] - task.table_top
            for r, q in zip(task.records, task.trajectory) if r["stage"] == "lift_hold"]
    result["minimum_lift_hold_clearance_m"] = min(lift) if lift else None
    return result

