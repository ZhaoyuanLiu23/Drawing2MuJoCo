"""Independent geometry, SI/inertia, composition, and actual contact tests."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from cad_mujoco.mesh import mass_properties, read_stl, write_stl
from cad_mujoco.metadata import mesh_unit, UP_ROTATIONS
from cad_mujoco.mjcf import attach_part, load_spec, save_spec
from cad_mujoco.pipeline import convert
from cad_mujoco.validation import initialize


ROOT = Path(__file__).resolve().parents[2]


def box(size, offset=(0, 0, 0)):
    """Outward oriented analytic cuboid; deliberately unrelated to benchmark sizes."""
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    vertices = vertices * size + offset
    faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
             [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
             [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return vertices[faces]


def fixture(root, size=(37, 23, 9), offset=(0, 0, 0), unit="mm"):
    source = root / "input"
    source.mkdir(exist_ok=True)
    mesh, parsed = source / "arbitrary.stl", source / "evidence.json"
    write_stl(mesh, box(size, offset))
    document = dict(status="generated_with_assumptions", units={"cad": unit}, recipe={"unit": unit},
                    inferences=["synthetic uncertainty retained"], conflicts=[{"status": "conflict", "raw": [11, 12]}])
    parsed.write_text(json.dumps(document), encoding="utf8")
    return mesh, parsed


class MeshTests(unittest.TestCase):
    def test_box_mass_and_inertia_analytic(self):
        size = np.array([0.043, 0.027, 0.011])
        p = mass_properties(box(size), density_kg_m3=4700)
        expected_mass = np.prod(size) * 4700
        np.testing.assert_allclose(p["com_m"], size / 2, atol=1e-14)
        self.assertAlmostEqual(p["mass_kg"], expected_mass)
        expected = expected_mass / 12 * (np.sum(size ** 2) - size ** 2)
        np.testing.assert_allclose(p["inertia_com_kg_m2"], np.diag(expected), atol=1e-16)

    def test_rotation_translation_and_nonzero_products_of_inertia(self):
        size = np.array([0.041, 0.026, 0.008])
        angle = 0.41
        r = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        base = mass_properties(box(size), mass_kg=0.083)
        shifted = box(size) @ r.T + [4, -7, 11]
        p = mass_properties(shifted, mass_kg=0.083)
        np.testing.assert_allclose(p["com_m"], r @ np.array(base["com_m"]) + [4, -7, 11], atol=1e-12)
        np.testing.assert_allclose(p["inertia_com_kg_m2"], r @ np.array(base["inertia_com_kg_m2"]) @ r.T, atol=1e-14)
        self.assertGreater(abs(p["inertia_com_kg_m2"][0][1]), 1e-7)

    def test_void_subtraction_integrals(self):
        # Signed surfaces: an offset enclosed void is an independent integral
        # oracle. Production STL gate intentionally rejects multiple shells.
        outer = box([0.06, 0.04, 0.02])
        void = box([0.018, 0.012, 0.008], [0.007, 0.005, 0.003])
        a = mass_properties(outer, density_kg_m3=3200)
        b = mass_properties(void, density_kg_m3=3200)
        cut = mass_properties(np.concatenate([outer, void[:, ::-1]]), density_kg_m3=3200)
        mass = a["mass_kg"] - b["mass_kg"]
        com = (a["mass_kg"] * np.array(a["com_m"]) - b["mass_kg"] * np.array(b["com_m"])) / mass
        def shifted(p):
            delta = np.array(p["com_m"]) - com
            return np.array(p["inertia_com_kg_m2"]) + p["mass_kg"] * (delta @ delta * np.eye(3) - np.outer(delta, delta))
        self.assertAlmostEqual(cut["mass_kg"], mass)
        np.testing.assert_allclose(cut["com_m"], com, atol=1e-14)
        np.testing.assert_allclose(cut["inertia_com_kg_m2"], shifted(a) - shifted(b), atol=1e-16)

    def test_invalid_meshes_are_rejected_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.stl"
            t = box([1, 2, 3])
            for broken in (t[:-1], np.concatenate([t, t + [5, 0, 0]]),
                           np.concatenate([t[:1, ::-1], t[1:]])):
                write_stl(path, broken)
                with self.assertRaises(ValueError):
                    read_stl(path)
            write_stl(path, t[:, ::-1])
            with self.assertRaisesRegex(ValueError, "positive signed volume"):
                mass_properties(read_stl(path), mass_kg=1)

    def test_explicit_mass_or_density_required(self):
        for kwargs in ({}, {"mass_kg": 1, "density_kg_m3": 1000}, {"mass_kg": 0}, {"density_kg_m3": float("nan")}):
            with self.assertRaises(ValueError):
                mass_properties(box([1, 1, 1]), **kwargs)


class ContractTests(unittest.TestCase):
    def test_drawing_inches_do_not_rescale_mm_export(self):
        document = dict(status="generated", units={"cad": "mm", "detected": ["in"]}, recipe={"unit": "mm"})
        self.assertEqual(mesh_unit(document)[0], "mm")
        with self.assertRaises(ValueError):
            mesh_unit(document, "in")

    def test_unknown_and_conflicting_units_block(self):
        document = {"status": "generated", "units": {"cad": "unknown"}}
        with self.assertRaises(ValueError):
            mesh_unit(document)
        self.assertEqual(mesh_unit(document, "in")[:2], ("in", "user_stl_unit"))
        document.update(units={"cad": "mm"}, recipe={"unit": "m"})
        with self.assertRaises(ValueError):
            mesh_unit(document)
        with self.assertRaises(ValueError):
            mesh_unit({"status": "needs_review"}, "mm")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_different_sizes_and_units_drop_and_change_properties(self):
        masses = []
        for index, size in enumerate(((37, 23, 9), (71, 32, 13))):
            with self.subTest(size=size):
                stl, parsed = fixture(self.root, size, offset=(12, -18, 7))
                out = self.root / ("case" + str(index))
                manifest, drop = convert(stl, parsed, out, density_kg_m3=5100, duration=1.5)
                self.assertTrue(drop["success"], drop["checks"])
                self.assertGreater(drop["first_table_contact_s"], 0.1)
                masses.append(manifest["rigid_body"]["mass_kg"])
                self.assertAlmostEqual(masses[-1], np.prod(size) * 1e-9 * 5100, places=10)
                self.assertEqual(parsed.read_bytes(), (out / "source_parsed.json").read_bytes())
                self.assertEqual(manifest["source_uncertainty"]["conflicts"][0]["raw"], [11, 12])
                self.assertFalse(manifest["collision"]["preserves_holes_and_slots"])
        self.assertGreater(masses[1], masses[0])
        stl, parsed = fixture(self.root, size=(0.037, 0.023, 0.009), unit="m")
        m, drop = convert(stl, parsed, self.root / "meters", density_kg_m3=5100, duration=1.5)
        self.assertTrue(drop["success"], drop["checks"])
        self.assertAlmostEqual(m["rigid_body"]["mass_kg"], masses[0], places=7)

    def test_axis_transform_compiled_inertia_and_unicode_relocation(self):
        stl, parsed = fixture(self.root, offset=(-3, 21, 17))
        out = self.root / "中文模型"
        m, drop = convert(stl, parsed, out, mass_kg=0.056, source_up_axis="y", duration=1.5)
        self.assertTrue(drop["success"], drop["checks"])
        model = load_spec(out / "part.xml").compile()
        bid = model.body("part").id
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, model.body_iquat[bid])
        actual_inertia = rotation.reshape(3, 3) @ np.diag(model.body_inertia[bid]) @ rotation.reshape(3, 3).T
        np.testing.assert_allclose(actual_inertia, m["rigid_body"]["inertia_com_kg_m2"], atol=1e-12)
        expected_extents = abs(UP_ROTATIONS["y"]) @ np.array([0.037, 0.023, 0.009])
        np.testing.assert_allclose(np.ptp(read_stl(out / "meshes/visual.stl").reshape(-1, 3), axis=0), expected_extents, atol=1e-8)
        self.assertEqual(model.nq, 7)
        self.assertEqual(model.nv, 6)
        self.assertEqual(model.geom("visual").contype[0], 0)
        self.assertEqual(model.geom("collision").contype[0], 1)
        moved = self.root / "relocated"
        out.rename(moved)
        self.assertGreater(load_spec(moved / "scene.xml").compile().nbody, 1)

    def test_pair_mismatch_and_stale_output_block(self):
        stl, parsed = fixture(self.root)
        out = self.root / "run"
        convert(stl, parsed, out, mass_kg=0.04, duration=1.5)
        doc = json.loads(parsed.read_text())
        doc["validation"] = {"stl": {"bounds_mm": [1, 2, 3]}}
        parsed.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "bounds disagree"):
            convert(stl, parsed, out, mass_kg=0.04)
        self.assertFalse((out / "part.xml").exists())
        self.assertFalse((out / "scene.xml").exists())
        self.assertEqual(json.loads((out / "manifest.json").read_text())["status"], "error")

    def test_existing_scene_keys_controls_assets_and_names_preserved(self):
        stl, parsed = fixture(self.root)
        out = self.root / "part"
        convert(stl, parsed, out, mass_kg=0.04, duration=1.5)
        spec = mujoco.MjSpec.from_string('''<mujoco><worldbody>
          <body name="robot"><joint name="hinge" type="hinge"/><geom size=".1"/></body>
          </worldbody><actuator><position name="servo" joint="hinge" kp="10"/></actuator>
          <keyframe><key name="home" qpos=".31" qvel=".02" ctrl=".29"/></keyframe></mujoco>''')
        attach_part(spec, out / "part.xml", [0.7, 0.2, 0.4])
        model = spec.compile()
        np.testing.assert_allclose(model.key_qpos[0, :1], [0.31])
        np.testing.assert_allclose(model.key_qvel[0, :1], [0.02])
        np.testing.assert_allclose(model.key_ctrl[0], [0.29])
        np.testing.assert_allclose(initialize(model).xpos[model.body("cad_part").id], [0.7, 0.2, 0.4])
        self.assertEqual(model.nu, 1)
        with self.assertRaisesRegex(ValueError, "already exists"):
            attach_part(spec, out / "part.xml", [0, 0, 1])
        save_spec(spec, self.root / "combined.xml")
        self.assertEqual(load_spec(self.root / "combined.xml").compile().nq, 8)

    def test_existing_cad_examples_keep_holes_in_mass_and_pass_drops(self):
        cases = ["washer/result", "plate/b2", "plate/b3", "bracket/result"]
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                source = ROOT / "examples" / case
                m, drop = convert(source / "model.stl", source / "parsed.json", self.root / str(index),
                                  density_kg_m3=2700, duration=1.5)
                self.assertTrue(drop["success"], drop["checks"])
                doc = json.loads((source / "parsed.json").read_text(encoding="utf8"))
                self.assertAlmostEqual(m["rigid_body"]["volume_m3"] * 1e9, doc["validation"]["stl"]["volume_mm3"], places=5)
                if case == "plate/b3":
                    self.assertEqual(m["source_uncertainty"]["conflicts"], doc["conflicts"])

    @unittest.skipUnless(os.environ.get("CAD_MUJOCO_PANDA_SCENE"), "Set CAD_MUJOCO_PANDA_SCENE for installed Panda integration")
    def test_actual_panda_scene_loads_and_part_drops(self):
        scene = Path(os.environ["CAD_MUJOCO_PANDA_SCENE"])
        original_bytes = scene.read_bytes()
        original = load_spec(scene).compile()
        source = ROOT / "examples/bracket/result"
        out = self.root / "panda"
        _, drop = convert(source / "model.stl", source / "parsed.json", out,
                          density_kg_m3=7800, base_scene=scene, duration=2)
        self.assertTrue(drop["success"], drop["checks"])
        model = load_spec(out / "scene.xml").compile()
        self.assertEqual(model.nq, original.nq + 7)
        self.assertEqual(model.nv, original.nv + 6)
        self.assertEqual(model.nu, original.nu)
        np.testing.assert_allclose(model.key_qpos[:, :original.nq], original.key_qpos)
        np.testing.assert_allclose(model.key_ctrl, original.key_ctrl)
        for i in range(1, original.nbody):
            name = mujoco.mj_id2name(original, mujoco.mjtObj.mjOBJ_BODY, i)
            if name:
                np.testing.assert_allclose(model.body(name).mass, original.body(name).mass, rtol=1e-5)
        self.assertEqual(scene.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
