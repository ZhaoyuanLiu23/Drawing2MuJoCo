"""Evidence-preserving conversion orchestrator; never imports drawing_cad."""
from pathlib import Path
import hashlib
import json

import mujoco
import numpy as np

from . import __version__
from .mesh import read_stl, mass_properties, write_stl
from .metadata import mesh_unit, verify_pair, UNIT_TO_M, UP_ROTATIONS
from .mjcf import part_xml, load_spec, make_drop_scene, save_spec
from .validation import validate_drop


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")


def convert(stl_path, parsed_path, output, *, density_kg_m3=None, mass_kg=None,
            stl_unit=None, source_up_axis="z", base_scene=None,
            table_top_m=(0.5, 0, 0.13), clearance_m=0.15, duration=5.0):
    stl_path, parsed_path, output = (Path(p).resolve() for p in (stl_path, parsed_path, output))
    for source in (stl_path, parsed_path, Path(base_scene).resolve() if base_scene else None):
        if source and (source == output or output in source.parents):
            raise ValueError("Input files must be outside the managed output directory")
    marker = output / ".cad-mujoco-output"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError("Choose an empty output directory or an existing cad_mujoco output")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text("cad_mujoco generated artifacts\n", encoding="utf8")
    for name in ("part.xml", "scene.xml", "manifest.json", "drop_test.json", "source_parsed.json"):
        (output / name).unlink(missing_ok=True)
    try:
        document = json.loads(parsed_path.read_text(encoding="utf8"))
        unit, unit_source, declarations = mesh_unit(document, stl_unit)
        if source_up_axis not in UP_ROTATIONS:
            raise ValueError("source_up_axis must be x, y or z")
        scale, rotation = UNIT_TO_M[unit], UP_ROTATIONS[source_up_axis]
        triangles = read_stl(stl_path) * scale
        properties = mass_properties(triangles, density_kg_m3=density_kg_m3, mass_kg=mass_kg)
        pairing = verify_pair(document, triangles, properties)
        original_com = np.asarray(properties["com_m"])
        transformed = (triangles - original_com) @ rotation.T
        inertia = rotation @ np.asarray(properties["inertia_com_kg_m2"]) @ rotation.T
        properties["inertia_com_kg_m2"] = inertia.tolist()
        properties["com_m"] = [0.0, 0.0, 0.0]
        bounds = [transformed.min(axis=(0, 1)).tolist(), transformed.max(axis=(0, 1)).tolist()]
        write_stl(output / "meshes" / "visual.stl", transformed)
        (output / "part.xml").write_text(part_xml(properties), encoding="utf8")
        part_model = load_spec(output / "part.xml").compile()
        if not np.isclose(part_model.body("part").mass[0], properties["mass_kg"], rtol=1e-8):
            raise ValueError("Compiled body mass does not match explicit mass")
        spec, scene_info = make_drop_scene(output / "part.xml", bounds, base_scene=base_scene,
                                          table_top_m=table_top_m, clearance_m=clearance_m)
        save_spec(spec, output / "scene.xml")
        # Byte-for-byte upstream evidence; no reinterpretation of dimensions or conflicts.
        (output / "source_parsed.json").write_bytes(parsed_path.read_bytes())
        manifest = dict(schema_version=__version__, status="generated", mujoco_version=mujoco.__version__,
                        inputs={"stl": dict(name=stl_path.name, sha256=hashlib.sha256(stl_path.read_bytes()).hexdigest()),
                                "parsed": dict(name=parsed_path.name, sha256=hashlib.sha256(parsed_path.read_bytes()).hexdigest())},
                        source_status=document["status"], input_pair_checks=pairing,
                        units=dict(stl_coordinate_unit=unit, source=unit_source, declarations=declarations,
                                   scale_to_m=scale, simulation_length="m", mass="kg", inertia="kg*m^2",
                                   drawing_unit_provenance=document.get("units", {})),
                        transform=dict(source_up_axis=source_up_axis, source_frame="right-handed CAD frame",
                                       source_frame_source="Drawing2CAD XY/+Z export contract" if source_up_axis == "z" else "user_axis_parameter",
                                       rotation_source_to_body=rotation.tolist(), source_com_coordinates=(original_com / scale).tolist(),
                                       source_origin_to_com_m=original_com.tolist(), body_frame="right-handed; origin at COM; +Z up",
                                       equation="body_m = R @ (stl_coordinates * scale_to_m - source_com_m)",
                                       bounds_body_m=bounds),
                        rigid_body=properties,
                        collision=dict(strategy="single_convex_hull", implementation="MuJoCo mesh collision convex hull",
                                       geom="cad_collision", visual_geom="cad_visual", visual_contact_enabled=False,
                                       preserves_holes_and_slots=False, mass_from_collision=False,
                                       friction=[0.8, 0.005, 0.0001], friction_source="test configuration; not measured material data"),
                        source_uncertainty={key: document.get(key, []) for key in ("inferences", "unknowns", "warnings", "conflicts")},
                        scene=dict(base_scene=str(Path(base_scene).resolve()) if base_scene else None, **scene_info),
                        assumptions=["Homogeneous rigid material; density or total mass is supplied by the caller.",
                                     "Mesh must be a single outward-oriented connected watertight surface; self-intersections are not tested.",
                                     "STL tessellation approximates CAD; inferred dimensions and conflicts remain upstream evidence.",
                                     "Convex collision fills cavities, holes and slots; valid for this drop scope, not insertion/assembly.",
                                     "Placement and contact parameters are test settings, not identified physical parameters."],
                        artifacts=dict(part="part.xml", scene="scene.xml", visual="meshes/visual.stl",
                                       source_evidence="source_parsed.json", validation="drop_test.json"))
        write_json(output / "manifest.json", manifest)
        result = validate_drop(output / "scene.xml", transformed, scene_info["table_top_m"], duration=duration)
        write_json(output / "drop_test.json", result)
        manifest["status"] = "validated" if result["success"] else "validation_failed"
        manifest["drop_success"] = result["success"]
        write_json(output / "manifest.json", manifest)
        return manifest, result
    except Exception as exc:
        for name in ("part.xml", "scene.xml"):
            (output / name).unlink(missing_ok=True)
        write_json(output / "manifest.json", dict(status="error", error=str(exc)))
        raise
