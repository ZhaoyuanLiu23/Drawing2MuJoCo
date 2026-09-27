"""Explicit-inertial MJCF plus VFS scene composition, independent of grasp code."""
from pathlib import Path
import hashlib
import xml.etree.ElementTree as ET

import mujoco
import numpy as np


def numbers(values):
    return " ".join(format(float(v), ".17g") for v in values)


def part_xml(properties):
    root = ET.Element("mujoco", model="cad_part")
    ET.SubElement(root, "compiler", angle="radian", inertiafromgeom="false")
    asset = ET.SubElement(root, "asset")
    ET.SubElement(asset, "mesh", name="part_mesh", file="meshes/visual.stl", inertia="exact")
    body = ET.SubElement(ET.SubElement(root, "worldbody"), "body", name="part")
    ET.SubElement(body, "freejoint", name="free")
    inertia = np.asarray(properties["inertia_com_kg_m2"])
    full = inertia[(0, 1, 2, 0, 0, 1), (0, 1, 2, 1, 2, 2)]
    ET.SubElement(body, "inertial", pos="0 0 0", mass=numbers([properties["mass_kg"]]),
                  fullinertia=numbers(full))
    ET.SubElement(body, "geom", name="visual", type="mesh", mesh="part_mesh", density="0",
                  contype="0", conaffinity="0", group="2", rgba="0.25 0.58 0.82 1")
    # MuJoCo mesh contacts use its computed convex hull; never treat this as an
    # exact concave collision surface. Explicit body inertia uses the actual STL.
    ET.SubElement(body, "geom", name="collision", type="mesh", mesh="part_mesh", density="0",
                  contype="1", conaffinity="1", group="3", friction="0.8 0.005 0.0001",
                  condim="3", solref="0.004 1", solimp="0.95 0.99 0.001", rgba="1 0.4 0.1 0.2")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


def load_spec(path):
    """Read XML/includes/assets via Python, also on Windows Unicode paths.

    Supports standard file-backed mesh/texture/hfield/skin assets. Asset directory
    semantics are relative to the main MJCF, like the MuJoCo compiler.
    Hash names prevent equal basenames from different assets being conflated.
    """
    path = Path(path).resolve()
    includes = {}

    def read_xml(filename, ancestors):
        if filename in ancestors:
            raise ValueError("Cyclic MJCF include")
        root = ET.fromstring(filename.read_bytes())
        for element in root.iter("include"):
            source = (filename.parent / element.attrib["file"]).resolve()
            name = "include_" + hashlib.sha256(str(source).encode()).hexdigest() + ".xml"
            includes[name] = read_xml(source, ancestors | {filename})
            element.set("file", name)
        return ET.tostring(root)

    xml = read_xml(path, set()).decode("utf8")
    spec = mujoco.MjSpec.from_string(xml, include=includes)
    assets = {}
    for elements, directory in ((spec.meshes, spec.compiler.meshdir),
                                (spec.textures, spec.compiler.texturedir),
                                (spec.hfields, ""), (spec.skins, "")):
        for element in elements:
            if not element.file:
                continue
            source = (path.parent / directory / element.file).resolve()
            blob = source.read_bytes()
            name = "asset_" + hashlib.sha256(blob).hexdigest() + source.suffix.lower()
            # Unnamed meshes/textures derive their names from the original file.
            element.name = element.name or Path(element.file).stem
            element.file = name
            assets[name] = blob
    spec.compiler.meshdir = ""
    spec.compiler.texturedir = ""
    spec.assets = assets
    return spec


def save_spec(spec, path):
    """Write a self-contained scene and relative assets, without changing originals."""
    path = Path(path)
    spec.compile()
    root = ET.fromstring(spec.to_xml())
    assets_dir = path.parent / "scene_assets"
    assets_dir.mkdir(exist_ok=True)
    for element in root.findall("asset/*"):
        filename = element.get("file")
        if filename:
            if filename not in spec.assets:
                raise ValueError("Cannot package an unresolved scene asset: " + filename)
            (assets_dir / filename).write_bytes(spec.assets[filename])
            element.set("file", "scene_assets/" + filename)
    ET.indent(root)
    path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf8")


def attach_part(scene_spec, part_path, position_m, prefix="cad_"):
    """Attach to an existing MjSpec, preserving its old DOFs, controls and keys.

    Returns the added free joint name. No table, controller or grasp changes here.
    """
    child = load_spec(part_path)
    child.body("part").pos = position_m
    for name, lookup in ((prefix + "part", scene_spec.body), (prefix + "free", scene_spec.joint)):
        if lookup(name) is not None:
            raise ValueError("CAD attachment name already exists: " + name)
    scene_spec.attach(child, frame=scene_spec.worldbody.add_frame(), prefix=prefix)
    model = scene_spec.compile()
    adr = int(model.joint(prefix + "free").qposadr[0])
    # MuJoCo 3.3.7 extends pre-existing keys with zero free translations. Fill only
    # the new joint so reset-to-home doesn't teleport the part under the table.
    for key in scene_spec.keys:
        qpos = list(key.qpos)
        qpos[adr:adr + 7] = model.qpos0[adr:adr + 7]
        key.qpos = qpos
    scene_spec.compile()
    return prefix + "free"


def make_drop_scene(part_path, bounds_m, *, base_scene=None, table_top_m=(0.5, 0, 0.13),
                    clearance_m=0.15):
    table_top = np.asarray(table_top_m, dtype=float)
    if table_top.shape != (3,) or not np.isfinite(table_top).all() or table_top[2] <= 0:
        raise ValueError("Table top must be finite XYZ with positive height")
    if not np.isfinite(clearance_m) or clearance_m <= 0:
        raise ValueError("Drop clearance must be finite and positive")
    if base_scene:
        spec = load_spec(base_scene)
    else:
        spec = mujoco.MjSpec.from_string('''<mujoco model="cad_drop_test">
          <worldbody><light pos="0 -1 2"/>
          <geom name="floor" type="plane" size="2 2 .01" rgba=".2 .25 .3 1"/>
          </worldbody></mujoco>''')
    if spec.geom("cad_test_table") is not None:
        raise ValueError("Base scene already contains cad_test_table")
    half_z = min(0.035, table_top[2] / 2)
    bounds = np.asarray(bounds_m)
    half_xy = np.maximum(0.25, np.max(np.abs(bounds[:, :2]), axis=0) * 1.5)
    spec.worldbody.add_geom(name="cad_test_table", type=mujoco.mjtGeom.mjGEOM_BOX,
                            pos=table_top - [0, 0, half_z], size=[*half_xy, half_z],
                            rgba=[0.7, 0.74, 0.78, 1], friction=[0.8, 0.005, 0.0001])
    position = table_top + [0, 0, clearance_m - bounds[0, 2]]
    attach_part(spec, part_path, position)
    # These settings apply exclusively to the newly exported test scene.
    impact_speed = np.sqrt(2 * 9.81 * clearance_m)
    # Resolve the thinnest feature envelope during the fastest falling motion.
    # This is geometric/time-step scaling, not a part-type or benchmark rule.
    step = min(spec.option.timestep, 0.00025, float(np.min(bounds[1] - bounds[0])) / (12 * impact_speed))
    if step < 1e-6:
        raise ValueError("Part/drop scale requires a timestep below the supported 1 microsecond limit")
    spec.option.timestep = step
    spec.geom("cad_collision").priority = 1
    spec.geom("cad_collision").solref = [8 * step, 1]
    spec.option.gravity = [0, 0, -9.81]
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.iterations = 100
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 5
    spec.option.enableflags |= int(mujoco.mjtEnableBit.mjENBL_MULTICCD)
    # 3.3.7's native mesh/box CCD can chatter on nearly coplanar thin meshes.
    # MPR + multi-contact is selected for this entire test scene, for every part.
    spec.option.disableflags |= int(mujoco.mjtDisableBit.mjDSBL_NATIVECCD)
    return spec, dict(table_top_m=table_top.tolist(), table_half_size_m=[*half_xy, half_z],
                      clearance_m=clearance_m, initial_com_world_m=position.tolist(),
                      timestep_s=step, contact_solref=[8 * step, 1], multiccd=True,
                      ccd_backend="MPR (nativeccd disabled in test scene)",
                      robot_policy="constant existing home-key controls; no grasp controller")
