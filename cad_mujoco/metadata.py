"""Unit and frame contract. Drawing units are distinct from exported STL units."""
import numpy as np


UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}
UP_ROTATIONS = {
    "z": np.eye(3),
    "y": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float),
    "x": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float),
}


def mesh_unit(document, explicit=None):
    if document.get("status") not in ("generated", "generated_with_assumptions"):
        raise ValueError("parsed.json does not describe a generated CAD model")
    declarations = {"units.cad": document.get("units", {}).get("cad"),
                    "recipe.unit": document.get("recipe", {}).get("unit")}
    known = {k: v for k, v in declarations.items() if v not in (None, "unknown")}
    if any(v not in UNIT_TO_M for v in known.values()):
        raise ValueError("Unsupported CAD/STL unit declaration")
    if len(set(known.values())) > 1:
        raise ValueError("Conflicting CAD/STL unit declarations")
    if explicit is not None:
        if explicit not in UNIT_TO_M or any(v != explicit for v in known.values()):
            raise ValueError("Explicit STL unit conflicts with parsed CAD metadata")
        return explicit, "user_stl_unit", declarations
    if not known:
        raise ValueError("STL unit is unknown; supply --stl-unit explicitly")
    return next(iter(known.values())), "+".join(known), declarations


def verify_pair(document, triangles_m, properties):
    validation = document.get("validation", {})
    if validation.get("valid_solid") is False or validation.get("solid_count", 1) != 1:
        raise ValueError("Input must represent one valid rigid solid")
    stl = validation.get("stl", {})
    bounds = stl.get("bounds_mm", validation.get("bounds_mm"))
    volume = stl.get("volume_mm3", validation.get("volume_mm3"))
    checks = {}
    if bounds is not None:
        actual = np.ptp(triangles_m.reshape(-1, 3), axis=0) * 1000
        if not np.allclose(actual, bounds, rtol=1e-4, atol=1e-5):
            raise ValueError("STL bounds disagree with parsed.json export validation")
        checks["bounds_match"] = True
    if volume is not None:
        if not np.isclose(properties["volume_m3"] * 1e9, volume, rtol=0.005, atol=1e-8):
            raise ValueError("STL volume disagrees with parsed.json export validation")
        checks["volume_match"] = True
    return checks
