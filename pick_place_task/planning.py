"""Metric target regions and rigid-transform placement, with no simulator state."""
from dataclasses import dataclass, asdict

import numpy as np

from manipulation.geometry import Pose
from manipulation.panda import PlanningFailure


@dataclass
class TaskConfig:
    transfer_clearance_m: float = .12
    release_gap_m: float = .0015
    zone_margin_m: float = .002
    center_tolerance_m: float = .01
    bottom_tolerance_m: float = .0025
    stability_tolerance_m: float = .0015
    move_seconds: float = 2.5
    place_seconds: float = 2.0
    release_seconds: float = 1.0
    retreat_seconds: float = 2.0
    verify_frames: int = 4
    verify_interval_s: float = .4

    def validate(self):
        if any(not np.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Task parameters must be finite and positive")
        if not isinstance(self.verify_frames, int) or self.verify_frames < 3:
            raise ValueError("Need at least three independent verification frames")
        if (self.verify_frames - 1) * self.verify_interval_s < 1:
            raise ValueError("Visual stability verification must span at least one second")
        if self.release_gap_m >= self.transfer_clearance_m:
            raise ValueError("Release gap must be smaller than transfer clearance")


@dataclass
class TargetZone:
    xy: np.ndarray
    size: np.ndarray
    table_top: float

    def __post_init__(self):
        self.xy = np.array(self.xy, dtype=float)
        self.size = np.array(self.size, dtype=float)
        if self.xy.shape != (2,) or self.size.shape != (2,) or not np.isfinite(np.r_[self.xy, self.size, self.table_top]).all():
            raise ValueError("Target zone requires finite world XY, XY size and table height")
        if np.any(self.size <= 0):
            raise ValueError("Target zone size must be positive")

    def json(self):
        return dict(center_xy_m=self.xy.tolist(), size_xy_m=self.size.tolist(), table_top_z_m=float(self.table_top),
                    frame="world", source="explicit task target; calibrated table height", name="target_zone")


def compose(a, b):
    return Pose(a.position + a.rotation @ b.position, a.rotation @ b.rotation)


def inverse(pose):
    return Pose(-pose.rotation.T @ pose.position, pose.rotation.T)


def world_bounds(vertices, pose):
    points = vertices @ pose.rotation.T + pose.position
    return np.array([points.min(0), points.max(0)])


def inside_zone(bounds, zone, margin=0.):
    return bool(np.all(bounds[0, :2] >= zone.xy - zone.size / 2 + margin)
                and np.all(bounds[1, :2] <= zone.xy + zone.size / 2 - margin))


def place_plan(vertices, observed_object, tcp_object, zone, current_tcp, config):
    """Keep observed orientation; align exterior-envelope center with requested XY.

    tcp_object is an estimated rigid grasp transform, not a physical attachment.
    All transforms are derived from vision, known geometry and robot encoders.
    """
    relative = world_bounds(vertices, Pose(np.zeros(3), observed_object.rotation))
    center = relative.mean(0)
    desired = Pose(np.r_[zone.xy - center[:2], zone.table_top + config.release_gap_m - relative[0, 2]],
                   observed_object.rotation.copy())
    bounds = world_bounds(vertices, desired)
    if not inside_zone(bounds, zone, config.zone_margin_m):
        raise PlanningFailure("target_zone_too_small_for_observed_footprint")
    place = compose(desired, inverse(tcp_object))
    height = max(current_tcp.position[2], place.position[2] + config.transfer_clearance_m)
    pre = Pose(np.r_[place.position[:2], height], place.rotation.copy())
    raise_pose = Pose(np.r_[current_tcp.position[:2], height], current_tcp.rotation.copy())
    if place.position[2] < zone.table_top:
        raise PlanningFailure("place_tcp_would_cross_table")
    return dict(object_release=desired, raise_pose=raise_pose, pre_place=pre, place=place)

