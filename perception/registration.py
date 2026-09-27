"""Dominant-plane and known CAD silhouette registration, without simulator state.

The V1 domain is a plate with a rectangular exterior and a visible broad face.
Plane orientation provides roll/pitch; the silhouette provides yaw/translation.
The actual CAD triangles score hypotheses, including holes and slots.
"""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import ConvexHull

from manipulation.geometry import Pose
from manipulation.panda import PlanningFailure


class PerceptionFailure(PlanningFailure):
    pass


@dataclass
class Estimate:
    pose: Pose
    points: np.ndarray
    hypotheses: list
    diagnostics: dict

    def json(self):
        return dict(**self.pose.json(), source="RGB-D + known CAD mesh", unit="m", frame="world",
                    quaternion_order="wxyz", diagnostics=self.diagnostics,
                    hypotheses=[dict(**pose.json(), silhouette_iou=float(score)) for pose, score in self.hypotheses])


def dominant_plane(points, camera_position, threshold):
    rng = np.random.default_rng(1729)  # Reproducible RANSAC, independent of part/test.
    sample = points[rng.choice(len(points), min(4000, len(points)), replace=False)]
    best = None
    for _ in range(120):
        a, b, c = sample[rng.choice(len(sample), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-12:
            continue
        n /= np.linalg.norm(n)
        keep = np.abs((sample - a) @ n) < threshold
        if best is None or keep.sum() > best.sum():
            best = keep
    if best is None or best.mean() < .65:
        raise PerceptionFailure("perception: no sufficiently visible dominant planar face")
    center = sample[best].mean(0)
    _, _, vh = np.linalg.svd(sample[best] - center, full_matrices=False)
    n = vh[-1]
    if n @ (camera_position - center) < 0:
        n *= -1
    keep = np.abs((points - center) @ n) < threshold
    return center, n, points[keep]


def minimum_rectangle(points):
    hull = points[ConvexHull(points).vertices]
    edges = np.roll(hull, -1, axis=0) - hull
    angles = np.arctan2(edges[:, 1], edges[:, 0])
    best = None
    for angle in angles:
        r = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        local = hull @ r
        low, high = local.min(0), local.max(0)
        area = np.prod(high - low)
        if best is None or area < best[0]:
            best = (area, r, low, high)
    _, r, low, high = best
    if (high - low)[0] < (high - low)[1]:
        r = r @ np.array([[0, -1], [1, 0]])
        local = hull @ r
        low, high = local.min(0), local.max(0)
    return (low + high) / 2 @ r.T, r, high - low


def silhouette_iou(frame, triangles, pose, observed):
    pixels = frame.project(triangles.reshape(-1, 3) @ pose.rotation.T + pose.position).reshape(-1, 3, 2)
    canvas = Image.new("1", (frame.depth.shape[1], frame.depth.shape[0]))
    draw = ImageDraw.Draw(canvas)
    for tri in pixels:
        draw.polygon([tuple(p) for p in tri], fill=1)
    mask = np.asarray(canvas, dtype=bool)
    return float((mask & observed).sum() / max(1, (mask | observed).sum()))


def estimate_pose(frame, triangles, geom_ids):
    triangles = np.asarray(triangles, dtype=float)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not np.isfinite(triangles).all():
        raise PerceptionFailure("perception: invalid known CAD mesh")
    observed = frame.mask(geom_ids)
    if observed.sum() < 200:
        raise PerceptionFailure("perception: insufficient segmented target pixels")
    if observed[0].any() or observed[-1].any() or observed[:, 0].any() or observed[:, -1].any():
        raise PerceptionFailure("perception: target clipped by camera field of view")
    points = frame.unproject(observed)
    if len(points) < .98 * observed.sum():
        raise PerceptionFailure("perception: invalid target depth")
    vertices = triangles.reshape(-1, 3)
    low, high = vertices.min(0), vertices.max(0)
    size = high - low
    thin = int(np.argmin(size))
    axes = sorted([i for i in range(3) if i != thin], key=lambda i: -size[i])
    if size[thin] >= .3 * min(size[axes]):
        raise PerceptionFailure("perception: V1 needs a thin plate with rectangular broad-face exterior")
    center, normal, plane_points = dominant_plane(points, frame.camera_position,
                                                   max(1e-6, min(size[thin] * .05, .0001)))
    x = np.array([1., 0., 0.])
    if abs(x @ normal) > .95:
        x = np.array([0., 1., 0.])
    x -= normal * (x @ normal)
    x /= np.linalg.norm(x)
    basis = np.column_stack((x, np.cross(normal, x)))
    uv = (plane_points - center) @ basis
    rectangle_center, r, extents = minimum_rectangle(uv)
    relative_extent_error = np.abs(extents - size[axes]) / size[axes]
    if np.max(relative_extent_error) > .05:
        raise PerceptionFailure("perception: observed outline does not match CAD extents (occlusion or unsupported geometry)")
    world_center = center + basis @ rectangle_center
    long_axis, short_axis = (basis @ r).T
    # Four orientation hypotheses: yaw half-turn and either broad face. No GT seed.
    hypotheses = []
    for face_sign in (1, -1):
        for yaw_sign in (1, -1):
            rotation = np.zeros((3, 3))
            rotation[:, axes[0]] = long_axis * yaw_sign
            rotation[:, thin] = normal * face_sign
            rotation[:, axes[1]] = short_axis
            if np.linalg.det(rotation) < 0:
                rotation[:, axes[1]] *= -1
            cad_center = (low + high) / 2
            cad_center[thin] = high[thin] if face_sign > 0 else low[thin]
            pose = Pose(world_center - rotation @ cad_center, rotation)
            score = silhouette_iou(frame, triangles, pose, observed)
            hypotheses.append((pose, score))
    hypotheses.sort(key=lambda item: -item[1])
    if hypotheses[0][1] < .90:
        raise PerceptionFailure("perception: CAD silhouette registration failed (occlusion or shape mismatch)")
    # Prefer a canonical representative when indistinguishable. Retain alternatives.
    plausible = [item for item in hypotheses if hypotheses[0][1] - item[1] < .008]
    plausible.sort(key=lambda item: (-item[0].rotation[thin, thin], -np.trace(item[0].rotation)))
    pose, score = plausible[0]
    diagnostics = dict(algorithm="RANSAC plane + minimum-area exterior rectangle + CAD triangle silhouette registration",
                       segmented_pixels=int(observed.sum()), point_count=len(points), plane_point_count=len(plane_points),
                       relative_extent_error=relative_extent_error.tolist(), silhouette_iou=score,
                       orientation_ambiguous=len(plausible) > 1, plausible_hypothesis_count=len(plausible),
                       ambiguity="indistinguishable broad-face/yaw hypotheses retained; canonical representative returned",
                       capture_time_s=frame.time, position_uncertainty_scale_m=float(np.median(frame.depth[observed]) / frame.focal),
                       visibility_requirement="dominant broad face and near-complete rectangular exterior",
                       pose_observation="one RGB-D frame; no target body pose, qpos or ground-truth seed")
    return Estimate(pose, points, plausible, diagnostics)
