"""Fixed world camera. The simulator produces pixels; only calibration leaves it."""
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from cad_mujoco.mjcf import load_spec, save_spec
from cad_mujoco.validation import initialize
from manipulation.geometry import matrix_quat, quat_matrix


@dataclass
class CameraConfig:
    width: int = 960
    height: int = 720
    fovy: float = 42.0
    # Installation relative to the table, never relative to the target body.
    offset_m: tuple = (0.0, -0.38, 0.65)
    name: str = "perception_rgbd"

    def validate(self):
        if any(not isinstance(v, int) or v <= 0 for v in (self.width, self.height)):
            raise ValueError("Camera image dimensions must be positive integers")
        offset = np.asarray(self.offset_m, dtype=float)
        if offset.shape != (3,) or not np.isfinite(offset).all() or np.linalg.norm(offset) < 1e-6:
            raise ValueError("Camera offset must be a finite nonzero XYZ vector in metres")
        if not np.isfinite(self.fovy) or not 1 < self.fovy < 170:
            raise ValueError("Camera vertical field of view must be between 1 and 170 degrees")


def add_camera(scene, destination, table_name, config):
    config.validate()
    spec = load_spec(scene)
    model = spec.compile()
    data = initialize(model)
    table = model.geom(table_name).id
    target = data.geom_xpos[table].copy() + [0, 0, model.geom_size[table, 2]]
    position = target + np.asarray(config.offset_m, dtype=float)
    z = position - target
    z /= np.linalg.norm(z)
    up = np.array([0., 0., 1.]) if abs(z[2]) < .99 else np.array([0., 1., 0.])
    x = np.cross(up, z)
    x /= np.linalg.norm(x)
    rotation = np.column_stack((x, np.cross(z, x), z))
    if spec.camera(config.name) is not None:
        raise ValueError("Perception camera name already exists")
    spec.worldbody.add_camera(name=config.name, pos=position, quat=matrix_quat(rotation), fovy=config.fovy)
    spec.visual.global_.offwidth = config.width
    spec.visual.global_.offheight = config.height
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    save_spec(spec, destination)


@dataclass
class Frame:
    rgb: np.ndarray
    depth: np.ndarray
    segmentation: np.ndarray
    camera_position: np.ndarray
    camera_rotation: np.ndarray
    focal: float
    time: float

    def mask(self, geom_ids):
        return np.isin(self.segmentation[:, :, 0], geom_ids) & (self.segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM))

    def unproject(self, mask):
        v, u = np.nonzero(mask & np.isfinite(self.depth) & (self.depth > 0))
        d = self.depth[v, u]
        h, w = self.depth.shape
        local = np.column_stack(((u - (w - 1) / 2) * d / self.focal,
                                 -(v - (h - 1) / 2) * d / self.focal, -d))
        return local @ self.camera_rotation.T + self.camera_position

    def project(self, points):
        local = (np.asarray(points) - self.camera_position) @ self.camera_rotation
        h, w = self.depth.shape
        return np.column_stack(((w - 1) / 2 - self.focal * local[:, 0] / local[:, 2],
                                (h - 1) / 2 + self.focal * local[:, 1] / local[:, 2]))


class RGBDCamera:
    def __init__(self, model, config):
        self.config = config
        self.id = model.camera(config.name).id
        if model.cam_bodyid[self.id] != 0 or model.cam_mode[self.id] != mujoco.mjtCamLight.mjCAMLIGHT_FIXED:
            raise ValueError("Perception requires a calibrated fixed world camera")
        self.position = model.cam_pos[self.id].copy()
        self.rotation = quat_matrix(model.cam_quat[self.id])
        self.focal = config.height / (2 * np.tan(np.deg2rad(model.cam_fovy[self.id]) / 2))
        self.renderer = mujoco.Renderer(model, height=config.height, width=config.width)

    def capture(self, data):
        r = self.renderer
        r.disable_depth_rendering()
        r.disable_segmentation_rendering()
        r.update_scene(data, camera=self.id)
        rgb = r.render().copy()
        r.enable_depth_rendering()
        depth = r.render().copy()  # MuJoCo Renderer already returns axial depth in metres.
        r.disable_depth_rendering()
        r.enable_segmentation_rendering()
        segmentation = r.render().copy()
        r.disable_segmentation_rendering()
        return Frame(rgb, depth, segmentation, self.position.copy(), self.rotation.copy(), self.focal, float(data.time))

    def close(self):
        self.renderer.close()
