"""Offline-only evaluation of recorded simulator states. Never a controller input."""
import numpy as np
from scipy.spatial import cKDTree

from manipulation.geometry import Pose, quat_matrix
from manipulation.panda import rotation_error


def state_pose(qpos, address):
    return Pose(np.asarray(qpos[address:address + 3]), quat_matrix(qpos[address + 3:address + 7]))


def pose_error(estimate, truth, triangles):
    """Raw error and CAD-verified symmetry-aware error; never choose a grasp using GT."""
    vertices = np.unique(triangles.reshape(-1, 3), axis=0)
    center = (vertices.min(0) + vertices.max(0)) / 2
    tree = cKDTree(vertices)
    thin = int(np.argmin(np.ptp(vertices, axis=0)))
    axes = [i for i in range(3) if i != thin]
    symmetries = []
    # Signed permutations exhaust axis-aligned rectangular/square plate symmetries.
    for swap in (False, True):
        order = list(range(3))
        if swap:
            order[axes[0]], order[axes[1]] = order[axes[1]], order[axes[0]]
        for signs in np.ndindex(2, 2, 2):
            r = np.eye(3)[:, order] @ np.diag(np.array(signs) * 2 - 1)
            if np.linalg.det(r) < .5:
                continue
            moved = (vertices - center) @ r.T + center
            # Strict geometric verification, not declaring every 180-degree yaw equivalent.
            if tree.query(moved)[0].max() < max(1e-7, np.ptp(vertices, axis=0).max() * 1e-5):
                symmetries.append(r)
    raw_angle = float(np.linalg.norm(rotation_error(estimate.rotation, truth.rotation)))
    angles = [float(np.linalg.norm(rotation_error(estimate.rotation, truth.rotation @ r))) for r in symmetries]
    return dict(position_error_m=float(np.linalg.norm(estimate.position - truth.position)),
                rotation_error_deg=float(np.rad2deg(raw_angle)),
                symmetry_aware_rotation_error_deg=float(np.rad2deg(min(angles))),
                verified_mesh_symmetry_count=len(symmetries),
                symmetry_method="proper signed axis permutations verified against all CAD vertices",
                ground_truth=truth.json(), evaluation_only=True)


def reconstruct_records(sim):
    address = int(sim.model.jnt_qposadr[sim.object_joint])
    for record, qpos in zip(sim.records, sim.trajectory):
        pose = state_pose(qpos, address)
        tcp = Pose(np.asarray(record["tcp_pose"]["position_m"]), quat_matrix(record["tcp_pose"]["quaternion_wxyz"]))
        record.update(object_pose=pose.json(), pose_source="offline simulator evaluation only",
                      bottom_z_m=float((sim.vertices @ pose.rotation.T + pose.position)[:, 2].min()),
                      relative_position_m=(tcp.rotation.T @ (pose.position - tcp.position)).tolist(),
                      relative_rotation=(tcp.rotation.T @ pose.rotation).tolist())

