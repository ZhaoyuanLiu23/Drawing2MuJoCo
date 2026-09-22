import json
import math
from pathlib import Path

import fitz
import numpy as np
import pytest
from PIL import Image

from drawing_cad.pipeline import run_pipeline


def run(path, **options):
    output = path.parent / (path.stem + "_" + path.suffix[1:] + "_result")
    return run_pipeline(path, output=output, **options)


def assert_model(document, output, outer, inner, thickness):
    assert document["status"] == "generated_with_assumptions", document
    p = document["parameters"]
    assert p["outer_diameter"]["value_mm"] == pytest.approx(outer)
    assert p["thickness"]["value_mm"] == pytest.approx(thickness)
    if inner is not None:
        assert p["inner_diameter"]["value_mm"] == pytest.approx(inner)
    else:
        assert document["recipe"]["hole_diameters_mm"] == []
    volume = math.pi/4 * (outer**2 - (inner or 0)**2) * thickness
    assert document["validation"]["volume_mm3"] == pytest.approx(volume)
    assert document["validation"]["step_reimport_valid"]
    assert document["validation"]["stl"]["watertight"]
    assert document["validation"]["stl"]["volume_mm3"] == pytest.approx(volume, rel=.005)
    for filename in ("parsed.json", "model.step", "model.stl", "preview.png", "preview.html",
                     "evidence.png", "parameters.json", "model.py"):
        assert (output/filename).stat().st_size > 50
    assert len(document["views"]) >= 2
    assert document["source"]["sha256"]
    # The machine-readable evidence must survive an actual JSON round trip.
    assert json.loads((output/"parsed.json").read_text(encoding="utf-8"))["status"] == document["status"]


@pytest.mark.parametrize("outer,inner,thickness", [(36,14,2), (52,23,4), (17,6,1.5), (36,None,3)])
def test_different_dimensions_and_topology(drawing_factory, outer, inner, thickness):
    path = drawing_factory(outer=outer, inner=inner, thickness=thickness)
    document, output = run(path)
    assert_model(document, output, outer, inner, thickness)


def test_annotations_override_drawing_scale(drawing_factory):
    # Change only printed dimensions. The raster geometry is deliberately unchanged.
    path = drawing_factory(label_values=dict(outer_diameter=41, inner_diameter=19, thickness=3.7))
    doc, output = run(path)
    assert_model(doc, output, 41, 19, 3.7)


@pytest.mark.parametrize("unit,note,factor", [("in",False,25.4), ("cm",True,10), ("mm",True,1)])
def test_units(drawing_factory, unit, note, factor):
    path = drawing_factory(outer=3,inner=1.2,thickness=.2,unit=unit,unit_note=note)
    doc, output = run(path)
    assert_model(doc, output, 3*factor, 1.2*factor, .2*factor)


@pytest.mark.parametrize("missing", ["outer_diameter", "inner_diameter", "thickness"])
def test_missing_dimensions_block_export(drawing_factory, missing):
    path = drawing_factory(omit=(missing,))
    doc, output = run(path)
    assert doc["status"] == "needs_review", doc
    assert doc["parameters"][missing]["status"] == "unknown"
    assert not (output/"model.step").exists()
    assert not (output/"model.stl").exists()
    assert (output/"parsed.json").exists()


def test_unknown_units_need_override(drawing_factory):
    path = drawing_factory(unit="")
    doc, output = run(path)
    assert doc["status"] == "needs_review"
    assert all(p["status"] == "unknown" for p in doc["parameters"].values())
    doc, output = run(path, units="mm")
    assert_model(doc, output, 36, 14, 2)
    assert doc["units"]["default_source"] == "cli_override"


def test_limits_and_stale_export_removal(drawing_factory):
    path = drawing_factory(limits=(1.8,2.2))
    doc, output = run(path)
    assert_model(doc, output, 36, 14, 2)
    assert doc["parameters"]["thickness"]["nominal_mm"] is None
    assert doc["parameters"]["thickness"]["nominal"] is None
    assert doc["parameters"]["thickness"]["min"] == 1.8
    assert doc["parameters"]["thickness"]["max"] == 2.2
    assert doc["parameters"]["thickness"]["inferred"]
    assert doc["parameters"]["thickness"]["value_source"] == "inferred_midpoint"
    assert [e["raw_text"] for e in doc["parameters"]["thickness"]["evidence"]] == ["2.2mm","1.8mm"]
    assert doc["parameters"]["thickness"]["limits_mm"] == [1.8,2.2]
    assert doc["recipe"]["contains_inferred_dimensions"] is True
    assert doc["recipe"]["inferred_dimension_names"] == ["thickness"]
    assert doc["recipe"]["dimension_usage"]["thickness"]["value_source"] == "inferred_midpoint"
    doc, output = run(path, limit_policy="require-nominal")
    assert doc["status"] == "needs_review"
    assert doc["parameters"]["thickness"]["nominal"] is None
    assert doc["parameters"]["thickness"]["min"] == 1.8
    assert doc["parameters"]["thickness"]["max"] == 2.2
    assert doc["parameters"]["thickness"]["value_source"] is None
    assert doc["parameters"]["thickness"]["inferred"] is False
    assert not (output/"model.step").exists()
    assert not (output/"preview.html").exists()


@pytest.mark.parametrize("extension", ["png", "jpg"])
def test_raster_ocr(drawing_factory, extension):
    path = drawing_factory(outer=32, inner=12, thickness=3)
    raster = path.with_suffix("."+extension)
    with fitz.open(path) as pdf:
        pix = pdf[0].get_pixmap(matrix=fitz.Matrix(2.5,2.5),alpha=False)
        image = Image.frombytes("RGB", [pix.width,pix.height], pix.samples)
    image.save(raster, quality=95)
    doc, output = run(raster)
    assert_model(doc, output, 32, 12, 3)
    assert any(d["source"] == "ocr" for d in doc["dimensions"])


def test_invalid_hole_dimensions(drawing_factory):
    path = drawing_factory(label_values=dict(inner_diameter=40))
    doc, output = run(path)
    assert doc["status"] == "needs_review"
    assert not (output/"model.step").exists()


def test_horizontal_side(drawing_factory):
    path = drawing_factory(side_orientation="horizontal")
    doc, output = run(path)
    assert_model(doc, output, 36, 14, 2)


def test_blank_and_unsupported_input(tmp_path):
    path = tmp_path/"empty.png"
    Image.new("RGB", (900,700), "white").save(path)
    doc, output = run(path)
    assert doc["status"] == "needs_review"
    assert doc["features"]["status"] == "unknown"
    assert not (output/"model.step").exists()
    path = tmp_path/"not_a_drawing.txt"
    path.write_text("36mm 14mm 2mm")
    doc, output = run(path)
    assert doc["status"] == "error"
    assert not (output/"model.step").exists()


def test_unowned_output_is_not_modified(drawing_factory, tmp_path):
    output = tmp_path/"user_data"
    output.mkdir()
    original = output/"model.step"
    original.write_text("user data")
    with pytest.raises(ValueError, match="not owned"):
        run_pipeline(drawing_factory(), output=output)
    assert original.read_text() == "user data"
