"""Synthetic drawings use arbitrary dimensions, independently of the supplied PDF."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit


def make_drawing(path, outer=36, inner=14, thickness=2, unit="mm", limits=None,
                 omit=(), unit_note=False, label_values=None, side_orientation="vertical"):
    """Draw dimension lines and independent, native PDF annotations (not a CAD model)."""
    c = canvas.Canvas(str(path), pagesize=(860, 620))
    c.setFont("Helvetica", 12)
    c.setLineWidth(0.8)
    c.drawString(60, 565, "SYNTHETIC ENGINEERING DRAWING - NOT TO SCALE")
    if unit_note:
        c.drawString(60, 540, "Dimensions in " + unit)
    cx, cy, r = 240, 320, 135
    c.circle(cx, cy, r)
    if inner is not None:
        c.circle(cx, cy, r * inner / outer)
    values = dict(outer_diameter=outer, inner_diameter=inner, thickness=thickness)
    values.update(label_values or {})

    def label(key, override=None):
        number = values[key] if override is None else override
        return f"{number:g}" + (unit if unit and not unit_note else "")

    def arrow(x, y, direction):
        c.line(x, y, x + direction * 6, y + 2)
        c.line(x, y, x + direction * 6, y - 2)

    def dimension(x0, x1, edge_y, baseline, key):
        c.line(x0, edge_y, x0, baseline + (10 if baseline > edge_y else -10))
        c.line(x1, edge_y, x1, baseline + (10 if baseline > edge_y else -10))
        text = label(key)
        half = c.stringWidth(text, "Helvetica", 12) / 2 + 7
        center = (x0 + x1) / 2
        c.line(x0, baseline, center-half, baseline)
        c.line(center+half, baseline, x1, baseline)
        arrow(x0, baseline, 1)
        arrow(x1, baseline, -1)
        if key not in omit:
            c.drawCentredString(center, baseline-4, text)

    dimension(cx-r, cx+r, cy, 490, "outer_diameter")
    if inner is not None:
        ri = r * inner / outer
        dimension(cx-ri, cx+ri, cy, 145, "inner_diameter")
    width = 2*r*thickness/outer
    if side_orientation == "vertical":
        x0, x1, y0, y1 = 600, 600+width, cy-r, cy+r
        c.rect(x0, y0, width, 2*r)
        c.line(x0, y0, x0, 125)
        c.line(x1, y0, x1, 125)
        c.line(x0-65, 145, x1+35, 145)
        arrow(x0, 145, -1)
        arrow(x1, 145, 1)
        if "thickness" not in omit:
            if limits:
                c.drawRightString(x0-12, 153, label("thickness", limits[1]))
                c.drawRightString(x0-12, 137, label("thickness", limits[0]))
            else:
                c.drawRightString(x0-12, 154, label("thickness"))
    else:
        # Bottom view aligned with the circular profile's horizontal projection.
        y0, y1 = 65, 65+width
        c.rect(cx-r, y0, 2*r, width)
        c.line(cx+r, y0, 440, y0)
        c.line(cx+r, y1, 440, y1)
        c.line(425, y0-25, 425, y1+25)
        if "thickness" not in omit:
            c.drawString(437, (y0+y1)/2-4, label("thickness"))
    c.showPage()
    c.save()
    return path


@pytest.fixture
def drawing_factory(tmp_path):
    counter = 0
    def create(**kwargs):
        nonlocal counter
        counter += 1
        return make_drawing(tmp_path / f"drawing_{counter}.pdf", **kwargs)
    return create
