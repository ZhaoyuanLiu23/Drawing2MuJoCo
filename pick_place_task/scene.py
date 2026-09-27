"""Add a non-physical target-zone marker to a new scene; never edit the input."""
import itertools
from pathlib import Path

import mujoco
import numpy as np

from cad_mujoco.mjcf import load_spec, save_spec
from cad_mujoco.validation import initialize
from .planning import TargetZone


def add_target_zone(source, destination, xy, size, table_name):
    spec = load_spec(source)
    model = spec.compile()
    data = initialize(model)
    table = model.geom(table_name).id
    rotation = data.geom_xmat[table].reshape(3, 3)
    if model.geom_type[table] != mujoco.mjtGeom.mjGEOM_BOX or not np.allclose(rotation[:, 2], [0, 0, 1], atol=1e-6):
        raise ValueError("Target zone requires a calibrated horizontal box table")
    top = float(data.geom_xpos[table, 2] + model.geom_size[table, 2])
    zone = TargetZone(xy, size, top)
    corners = np.array([np.r_[zone.xy + np.array(signs) * zone.size / 2, top] for signs in itertools.product([-1, 1], repeat=2)])
    local = (corners - data.geom_xpos[table]) @ rotation
    if np.any(np.abs(local[:, :2]) > model.geom_size[table, :2] - .001):
        raise ValueError("target_zone_outside_table")
    if spec.site("target_zone") is not None:
        raise ValueError("Scene already contains a target_zone marker")
    # A site has no collision/inertia. Its thin translucent volume only marks the task.
    spec.worldbody.add_site(name="target_zone", type=mujoco.mjtGeom.mjGEOM_BOX,
                           pos=[*zone.xy, top + .0001], size=[*(zone.size / 2), .00005], rgba=[.15, .85, .25, .2])
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    save_spec(spec, destination)
    return zone

