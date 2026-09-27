"""Binary STL validation and signed tetrahedron volume integrals (no CAD import)."""
from pathlib import Path
import struct

import numpy as np


STL_DTYPE = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)),
                      ("attribute", "<u2")])


def read_stl(path):
    raw = Path(path).read_bytes()
    if len(raw) < 84:
        raise ValueError("Truncated binary STL")
    count = struct.unpack_from("<I", raw, 80)[0]
    if count == 0 or len(raw) != 84 + 50 * count:
        raise ValueError("Expected a binary STL exported by Drawing2CAD")
    triangles = np.frombuffer(raw, STL_DTYPE, offset=84)["vertices"].astype(float)
    if not np.isfinite(triangles).all():
        raise ValueError("STL has non-finite coordinates")
    vertices, indices = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = indices.reshape(-1, 3)
    span = np.ptp(vertices, axis=0)
    if np.any(span <= 0):
        raise ValueError("STL must enclose a three-dimensional volume")
    area2 = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                   triangles[:, 2] - triangles[:, 0]), axis=1)
    if np.any(area2 <= np.finfo(float).eps * max(span) ** 2):
        raise ValueError("STL has degenerate triangles")
    edges = {}
    neighbours = [[] for _ in faces]
    for i, face in enumerate(faces):
        for a, b in zip(face, np.roll(face, -1)):
            edges.setdefault(tuple(sorted((a, b))), []).append((i, a < b))
    for incidence in edges.values():
        if len(incidence) != 2 or incidence[0][1] == incidence[1][1]:
            raise ValueError("STL must be watertight with consistent winding")
        a, b = incidence[0][0], incidence[1][0]
        neighbours[a].append(b)
        neighbours[b].append(a)
    seen, stack = {0}, [0]
    while stack:
        for n in neighbours[stack.pop()]:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    if len(seen) != len(faces):
        raise ValueError("Only one connected surface shell is supported")
    return triangles


def mass_properties(triangles_m, *, density_kg_m3=None, mass_kg=None):
    """Integrate the actual closed surface, including through-hole/slot voids.

    Each oriented surface triangle and the reference origin form a signed
    tetrahedron. Integral(x*x^T) = V/20 * (sum(v)*sum(v)^T + sum(v*v^T)).
    Subtract V*c*c^T before converting second moments to the COM inertia tensor.
    """
    if (density_kg_m3 is None) == (mass_kg is None):
        raise ValueError("Supply exactly one of density_kg_m3 or mass_kg")
    supplied = density_kg_m3 if density_kg_m3 is not None else mass_kg
    if not np.isfinite(supplied) or supplied <= 0:
        raise ValueError("Density/mass must be finite and positive")
    reference = (triangles_m.min(axis=(0, 1)) + triangles_m.max(axis=(0, 1))) / 2
    t = triangles_m - reference
    dv = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])) / 6
    volume = float(dv.sum())
    if not np.isfinite(volume) or volume <= 0:
        raise ValueError("STL must have positive signed volume (outward normals)")
    sums = t.sum(axis=1)
    com = np.einsum("i,ij->j", dv, sums) / (4 * volume)
    second = np.einsum("i,ij,ik->jk", dv, sums, sums)
    second += np.einsum("i,iaj,iak->jk", dv, t, t)
    central = second / 20 - volume * np.outer(com, com)
    density = density_kg_m3 if density_kg_m3 is not None else mass_kg / volume
    inertia = density * (np.trace(central) * np.eye(3) - central)
    moments = np.linalg.eigvalsh(inertia)
    if not np.isfinite(inertia).all() or moments[0] <= 0 or moments[2] > sum(moments[:2]) * (1 + 1e-8):
        raise ValueError("STL produced a non-physical inertia tensor")
    return dict(volume_m3=volume, mass_kg=float(density * volume),
                density_kg_m3=float(density), com_m=(com + reference).tolist(),
                inertia_com_kg_m2=inertia.tolist(),
                mass_source="user_density_times_mesh_volume" if mass_kg is None else "user_mass",
                inertia_source="signed_tetrahedron_integrals_of_STL_uniform_density")


def write_stl(path, triangles):
    records = np.zeros(len(triangles), dtype=STL_DTYPE)
    records["vertices"] = triangles
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    records["normal"] = normal / np.linalg.norm(normal, axis=1)[:, None]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"cad_mujoco SI mesh".ljust(80, b" ") +
                           struct.pack("<I", len(records)) + records.tobytes())
