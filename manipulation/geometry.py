"""Metric geometry, ground-truth poses and opposed exterior-edge candidates."""
from dataclasses import dataclass
import itertools

import mujoco
import numpy as np


def quat_matrix(quaternion):
    result = np.empty(9)
    mujoco.mju_quat2Mat(result, np.asarray(quaternion, dtype=float))
    return result.reshape(3, 3)


def matrix_quat(matrix):
    result = np.empty(4)
    mujoco.mju_mat2Quat(result, np.asarray(matrix).ravel())
    return result


@dataclass
class Pose:
    position: np.ndarray
    rotation: np.ndarray

    def json(self):
        return dict(position_m=self.position.tolist(), quaternion_wxyz=matrix_quat(self.rotation).tolist())


def object_pose(data, body_id):
    return Pose(data.xpos[body_id].copy(), quat_matrix(data.xquat[body_id]))


def geom_vertices(model, geom_id):
    kind = model.geom_type[geom_id]
    if kind == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = model.geom_dataid[geom_id]
        start = model.mesh_vertadr[mesh]
        return model.mesh_vert[start:start + model.mesh_vertnum[mesh]].astype(float)
    if kind == mujoco.mjtGeom.mjGEOM_BOX:
        return np.array(list(itertools.product([-1, 1], repeat=3))) * model.geom_size[geom_id]
    raise ValueError("Only box/mesh geometry is supported by this Panda adapter")


def object_triangles(model, body_id):
    meshes = [i for i in range(model.ngeom) if model.geom_bodyid[i] == body_id
              and model.geom_type[i] == mujoco.mjtGeom.mjGEOM_MESH]
    visual = [i for i in meshes if model.geom_contype[i] == 0 and model.geom_conaffinity[i] == 0]
    choices = visual or meshes
    if len(choices) != 1:
        raise ValueError("Target must have one visual STL mesh on its rigid body")
    g = choices[0]
    mesh = model.geom_dataid[g]
    a = model.mesh_faceadr[mesh]
    faces = model.mesh_face[a:a + model.mesh_facenum[mesh]]
    vertices = geom_vertices(model, g) @ quat_matrix(model.geom_quat[g]).T + model.geom_pos[g]
    return vertices[faces]


def _on_triangle(point, triangles, tolerance=1e-7):
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    v0, v1, v2 = b - a, c - a, point - a
    cross = lambda u, v: u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    den = cross(v0, v1)
    good = np.abs(den) > 1e-15
    if not np.any(good):
        return False
    u = cross(v2, v1)[good] / den[good]
    v = cross(v0, v2)[good] / den[good]
    return bool(np.any((u >= -tolerance) & (v >= -tolerance) & (u + v <= 1 + tolerance)))


@dataclass
class Candidate:
    id: str
    local_tip: np.ndarray
    local_rotation: np.ndarray
    contact_points: np.ndarray
    width_m: float
    clearance_m: float
    score: float

    def poses(self, pose):
        rotation = pose.rotation @ self.local_rotation
        grasp = Pose(pose.position + pose.rotation @ self.local_tip, rotation)
        pre = Pose(grasp.position - rotation[:, 2] * self.clearance_m, rotation)
        return pre, grasp

    def json(self, pose):
        pre, grasp = self.poses(pose)
        return dict(id=self.id, width_m=self.width_m, local_tip_m=self.local_tip.tolist(),
                    local_contact_points_m=self.contact_points.tolist(),
                    local_rotation=self.local_rotation.tolist(), pre_grasp=pre.json(), grasp=grasp.json(),
                    source="mesh exterior planar faces + current ground-truth body pose", score=self.score)


def generate_candidates(triangles, pose, calibration, *, pregrasp_clearance=0.10, table_clearance=0.0007):
    lo, hi = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
    spans = hi - lo
    thin = int(np.argmin(spans))
    planar = [i for i in range(3) if i != thin]
    normal = pose.rotation[:, thin]
    if normal[2] < 0.98 or spans[thin] >= min(spans[planar]) * 0.5:
        return [], ["unsupported_orientation_or_non_plate: require a nearly horizontal flat plate"]
    recess = calibration["pad_tip_recess_m"]
    if spans[thin] < table_clearance + recess + 0.001:
        return [], ["too_thin_for_finger_pads_with_table_clearance"]
    tip_height = max(table_clearance, min(spans[thin] * 0.25, spans[thin] - recess - 0.001))
    contact_z = lo[thin] + tip_height + recess + min(0.003, (spans[thin] - tip_height - recess) / 2)
    candidates, rejected = [], []
    for closing in planar:
        tangent = next(i for i in planar if i != closing)
        width = float(spans[closing])
        if width + 0.006 > calibration["open_gap_m"]:
            rejected.append(f"axis_{closing}: exceeds measured gripper opening")
            continue
        if spans[tangent] < 2 * calibration["pad_half_width_m"]:
            rejected.append(f"axis_{closing}: edge too short for finger pads")
            continue
        # Normalized geometric samples, never a part dimension or feature count.
        for offset in (0.0, -0.22, 0.22):
            center = (lo + hi) / 2
            center[tangent] += offset * spans[tangent]
            center[thin] = lo[thin] + tip_height
            contacts = []
            supported = True
            for edge in (lo[closing], hi[closing]):
                point = center.copy()
                point[closing], point[thin] = edge, contact_z
                mask = np.all(np.abs(triangles[:, :, closing] - edge) < max(spans) * 1e-5, axis=1)
                faces = triangles[mask][:, :, [tangent, thin]]
                if not len(faces) or not _on_triangle(point[[tangent, thin]], faces):
                    supported = False
                contacts.append(point)
            if not supported:
                rejected.append(f"axis_{closing}/offset_{offset}: no opposed exterior surface support")
                continue
            for sign in (1, -1):
                z = -np.eye(3)[:, thin]
                y = sign * np.eye(3)[:, closing]
                rotation = np.column_stack((np.cross(y, z), y, z))
                candidates.append(Candidate(f"edge_{closing}_{offset:g}_{sign}", center.copy(), rotation,
                                            np.array(contacts), width, pregrasp_clearance,
                                            abs(offset) + width))
    return sorted(candidates, key=lambda c: c.score), rejected
