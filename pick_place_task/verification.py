"""Task success from new RGB-D estimates only; no simulator-state arguments."""
import numpy as np

from .planning import inside_zone, world_bounds


def verify_estimates(observations, vertices, zone, config, *, after_time, released, retreated, initial_pose):
    checks = dict(fresh_observations=False, complete_footprint_inside_zone=False,
                  center_on_target=False, near_table=False, visually_stable=False,
                  released=bool(released), retreated=bool(retreated), visually_relocated=False)
    if len(observations) < config.verify_frames:
        return dict(success=False, checks=checks, reason="insufficient_visual_verification_frames")
    times = np.array([e.diagnostics["capture_time_s"] for e in observations])
    bounds = np.array([world_bounds(vertices, e.pose) for e in observations])
    centers = bounds.mean(1)
    center_errors = np.linalg.norm(centers[:, :2] - zone.xy, axis=1)
    shift = float(np.max(np.linalg.norm(centers - centers[0], axis=1)))
    envelope_shift = float(np.max(np.linalg.norm(bounds - bounds[0], axis=2)))
    initial_center = world_bounds(vertices, initial_pose).mean(0)
    checks.update(fresh_observations=bool(times[0] > after_time and np.all(np.diff(times) >= config.verify_interval_s * .99)),
                  complete_footprint_inside_zone=all(inside_zone(b, zone, config.zone_margin_m) for b in bounds),
                  center_on_target=bool(np.max(center_errors) <= config.center_tolerance_m),
                  near_table=bool(np.max(np.abs(bounds[:, 0, 2] - zone.table_top)) <= config.bottom_tolerance_m),
                  visually_stable=shift <= config.stability_tolerance_m and envelope_shift <= 2 * config.stability_tolerance_m,
                  visually_relocated=bool(np.linalg.norm(centers[-1, :2] - initial_center[:2]) > config.center_tolerance_m))
    return dict(success=all(checks.values()), checks=checks, reason=None if all(checks.values()) else "visual_task_checks_failed",
                failed_checks=[key for key, value in checks.items() if not value],
                center_error_m=float(center_errors[-1]), maximum_center_error_m=float(center_errors.max()),
                maximum_center_drift_m=shift, maximum_envelope_drift_m=envelope_shift,
                duration_s=float(times[-1] - times[0]), observed_centers_m=centers.tolist(),
                bottom_z_m=bounds[:, 0, 2].tolist(), source="fresh post-release RGB-D estimates only")
