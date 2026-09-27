"""A real contact-driven drop, with no pose writes after initialization."""
import mujoco
import numpy as np

from .mjcf import load_spec


def initialize(model):
    data = mujoco.MjData(model)
    home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if home >= 0:
        mujoco.mj_resetDataKeyframe(model, data, home)
    else:
        mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    return data


def validate_drop(scene_path, vertices_m, table_top_m, *, duration=5.0):
    if not np.isfinite(duration) or duration < 1:
        raise ValueError("Drop validation duration must be at least one second")
    model = load_spec(scene_path).compile()
    data = initialize(model)
    body = model.body("cad_part").id
    joint = model.joint("cad_free")
    dof = int(joint.dofadr[0])
    collision = model.geom("cad_collision").id
    table = model.geom("cad_test_table").id
    vertices = np.unique(vertices_m.reshape(-1, 3), axis=0)
    initial_z = float(data.xpos[body, 2])
    first_contact, minimum_bottom = None, float("inf")
    tail, trace = [], []
    initial_contacts = data.ncon
    initial_overlap = any(collision in (c.geom1, c.geom2) and c.dist < 0 for c in data.contact)
    other_contacts = 0
    steps = int(np.ceil(duration / model.opt.timestep))
    for i in range(steps):
        mujoco.mj_step(model, data)
        mujoco.mj_forward(model, data)
        if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all() or np.any(data.warning.number):
            raise RuntimeError("Non-finite state or MuJoCo numerical warning in drop test")
        contact = any({c.geom1, c.geom2} == {collision, table} for c in data.contact)
        other_contacts += sum(collision in (c.geom1, c.geom2) and table not in (c.geom1, c.geom2)
                              for c in data.contact)
        if contact and first_contact is None:
            first_contact = float(data.time)
        bottom = float((vertices @ data.xmat[body].reshape(3, 3)[2]).min() + data.xpos[body, 2])
        minimum_bottom = min(minimum_bottom, bottom)
        speed = float(np.linalg.norm(data.qvel[dof:dof + 3]))
        angular = float(np.linalg.norm(data.qvel[dof + 3:dof + 6]))
        row = [float(data.time), float(data.xpos[body, 2]), speed, angular, bottom, bool(contact)]
        if data.time >= duration - 0.5:
            tail.append(row)
        if i % max(1, round(0.02 / model.opt.timestep)) == 0:
            trace.append(row)
    values = np.asarray(tail, dtype=float)
    top = float(table_top_m[2])
    thickness = float(np.ptp(vertices, axis=0)[2])
    penetration_tolerance = min(0.001, thickness * 0.1)
    impact_penetration_tolerance = min(0.002, thickness * 0.5)
    initial_bottom = float(vertices[:, 2].min() + initial_z)
    clearance = initial_bottom - top
    checks = dict(started_above_table=clearance > 0,
                  no_initial_part_overlap=not initial_overlap,
                  no_other_object_contacts=other_contacts == 0,
                  fell_under_gravity=initial_z - float(data.xpos[body, 2]) >= clearance * 0.8,
                  table_contact=first_contact is not None,
                  sustained_table_contact=float(values[:, 5].mean()) >= 0.95,
                  settled_linear_speed=float(values[:, 2].max()) < 0.001,
                  settled_angular_speed=float(values[:, 3].max()) < 0.01,
                  stable_height=float(np.ptp(values[:, 1])) < 0.0001,
                  no_excess_penetration=minimum_bottom >= top - impact_penetration_tolerance,
                  resting_on_table=abs(float(values[-1, 4]) - top) < penetration_tolerance,
                  no_numerical_warnings=not bool(np.any(data.warning.number)))
    return dict(success=all(checks.values()), checks=checks, simulated_seconds=float(data.time),
                first_table_contact_s=first_contact, initial_com_z_m=initial_z,
                final_com_world_m=data.xpos[body].tolist(), final_linear_speed_m_s=speed,
                final_angular_speed_rad_s=angular, minimum_bottom_z_m=minimum_bottom,
                table_top_z_m=top, penetration_tolerance_m=penetration_tolerance,
                impact_penetration_tolerance_m=impact_penetration_tolerance,
                tail_contact_fraction=float(values[:, 5].mean()),
                tail_max_linear_speed_m_s=float(values[:, 2].max()),
                tail_height_range_m=float(np.ptp(values[:, 1])), initial_scene_contacts=int(initial_contacts),
                trace_columns=["time_s", "com_z_m", "speed_m_s", "angular_speed_rad_s", "bottom_z_m", "table_contact"],
                trace=trace)
