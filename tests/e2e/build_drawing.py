"""CAD-interpreter stage of E2E: create a fresh engineering PDF, then parse it.

Only this test generator knows the fixture dimensions. No recipe is supplied to
Drawing2CAD and no existing STL/MJCF is an input to this stage.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/drawing2cad"))
from slot_only_fixture import draw_slot_only
from drawing_cad.pipeline import run_pipeline


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    spec = dict(width=108., height=32., thickness=8., slots=((28., 16., 22., 9.), (80., 16., 22., 9.)))
    (root / "drawing_fixture.json").write_text(json.dumps(dict(unit="mm", purpose="independent synthetic engineering drawing", **spec), indent=2))
    drawing = draw_slot_only(root / "input.pdf", **spec)
    doc, out = run_pipeline(drawing, output=root / "drawing2cad", dpi=144, ocr="never")
    if doc["status"] != "generated_with_assumptions":
        raise AssertionError(doc.get("blocking_reasons", doc.get("error", doc["status"])))
    # Independent drawing author values validate outer dimensions; inferred slots
    # remain explicitly inferred and are not quietly replaced by fixture answers.
    import numpy as np
    np.testing.assert_allclose(doc["validation"]["bounds_mm"],
                               [spec["width"], spec["height"], spec["thickness"]], atol=1e-5)
    assert doc["validation"]["step_reimport_valid"] and doc["validation"]["stl"]["watertight"]
