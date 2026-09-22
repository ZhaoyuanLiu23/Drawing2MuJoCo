"""Synthetic drawings with variable circle/slot layouts.

The drawing generator is deliberately data driven.  Counts, locations and
dimension-chain labels all come from the supplied specification so these
tests cannot be satisfied by a fixed 8-hole/2-slot production path.
"""
from matplotlib.font_manager import findfont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


DEFAULT_SIZES = dict(
    plate_width=200.0,
    plate_height=80.0,
    thickness=3.0,
    slot_width=10.0,
    slot_length=25.0,
    hole_diameter=4.0,
    countersink_diameter=8.0,
    countersink_angle=90.0,
)


def _chain(total, positions):
    points = [0.0, *sorted(set(positions)), float(total)]
    return [b - a for a, b in zip(points, points[1:])]


def draw_variable_pattern_pdf(path, holes, slots, *, sizes=None, draw_slot_y=True):
    """Draw a plate whose physical feature coordinates are supplied directly.

    ``holes`` and ``slots`` are ``(x, y_from_top)`` pairs in the explicitly
    supplied test unit.  Their projected pixel positions are computed from the
    plate dimensions, while all physical values remain drawing annotations.
    """
    values = dict(DEFAULT_SIZES, **(sizes or {}))
    width, height = values['plate_width'], values['plate_height']
    left, top, right, bottom = 80.0, 120.0, 420.0, 260.0

    def project(point):
        x, y = point
        return left + x / width * (right-left), top + y / height * (bottom-top)

    hole_pixels = [project(point) for point in holes]
    slot_pixels = [project(point) for point in slots]
    hole_x = sorted(set(x for x, _ in holes)); hole_y = sorted(set(y for _, y in holes))
    slot_x = sorted(set(x for x, _ in slots)); slot_y = sorted(set(y for _, y in slots))
    hole_x_px = [project((x, 0))[0] for x in hole_x]
    hole_y_px = [project((0, y))[1] for y in hole_y]
    slot_x_px = [project((x, 0))[0] for x in slot_x]
    slot_y_px = [project((0, y))[1] for y in slot_y]

    pdfmetrics.registerFont(TTFont('VariableDrawing', findfont('DejaVu Sans')))
    c = canvas.Canvas(str(path), pagesize=(500, 500), pageCompression=0)

    def line(x, y, X, Y, shade=.35, line_width=.45):
        c.setStrokeGray(shade); c.setLineWidth(line_width); c.line(x, 500-y, X, 500-Y)

    def text(x, y, word, size=10, vertical=False):
        c.saveState(); c.setFillGray(0); c.setFont('VariableDrawing', size)
        c.translate(x, 500-y)
        if vertical:
            c.rotate(90)
        c.drawCentredString(0, 0, str(word)); c.restoreState()

    def rectangle(x, y, X, Y, fill=.80):
        c.setStrokeGray(0); c.setLineWidth(1); c.setFillGray(fill)
        c.rect(x, 500-Y, X-x, Y-y, fill=1, stroke=1)

    def cross(x, y, radius):
        line(x-radius, y, x+radius, y, .35, .4)
        line(x, y-radius, x, y+radius, .35, .4)

    def label(value):
        return f'{value:g}'

    def dimx(pixel_positions, physical_values, y, anchor):
        line(pixel_positions[0], y, pixel_positions[-1], y)
        for x in pixel_positions:
            line(x, y-7, x, anchor)
        for lo, hi, value in zip(pixel_positions, pixel_positions[1:], physical_values):
            text((lo+hi)/2, y-5, label(value))

    def dimy(pixel_positions, physical_values, x, anchor):
        line(x, pixel_positions[0], x, pixel_positions[-1])
        for y in pixel_positions:
            line(x-6, y, anchor, y)
        for lo, hi, value in zip(pixel_positions, pixel_positions[1:], physical_values):
            text(x-5, (lo+hi)/2, label(value), vertical=True)

    text(250, 24, 'VARIABLE FEATURE LAYOUT / NOT TO SCALE', 10)
    rectangle(left, top, right, bottom)
    for x, y in hole_pixels:
        c.setStrokeGray(0); c.setLineWidth(.7)
        c.circle(x, 500-y, 6, fill=0); c.circle(x, 500-y, 3, fill=0)
        cross(x, y, 10)
    for x, y in slot_pixels:
        c.setFillGray(1); c.setStrokeGray(0); c.setLineWidth(1)
        c.roundRect(x-20, 500-(y+9), 40, 18, 9, fill=1, stroke=1)
        cross(x, y, 15)

    # A centre line is geometric evidence only for features actually on it.
    line(left-12, (top+bottom)/2, right+12, (top+bottom)/2, .45, .3)
    rectangle(left, 330, right, 336)
    for x in hole_x_px:
        line(x, 326, x, 340)

    dimx([left, right], [width], 55, top)
    dimx([left, *hole_x_px, right], _chain(width, hole_x), 84, top)
    dimy([top, bottom], [height], 20, left)
    dimy([top, *hole_y_px, bottom], _chain(height, hole_y), 48, left)
    dimx([left, *slot_x_px, right], _chain(width, slot_x), 292, bottom)
    if draw_slot_y:
        dimy([top, *slot_y_px, bottom], _chain(height, slot_y), 458, right)

    line(45, 307, 45, 352); line(40, 330, left, 330); line(40, 336, left, 336)
    text(39, 320, label(values['thickness']), vertical=True)

    # Counted callouts are generated from the detected-layout specification.
    first_slot_x, first_slot_y = slot_pixels[0]
    text(250, 310, f"{len(slots)}-SLOT({label(values['slot_width'])}x{label(values['slot_length'])})", 8)
    line(220, 313, 196, 313); line(196, 313, first_slot_x, first_slot_y+9)
    target_hole_x, target_hole_y = hole_pixels[-1]
    text(426, 362, f"{len(holes)}-Ø{label(values['hole_diameter'])}(Ø{label(values['countersink_diameter'])})", 8)
    line(408, 364, 380, 364); line(380, 364, target_hole_x, target_hole_y+6)
    text(426, 376, f"CSK {label(values['countersink_angle'])}°", 8)

    # Generic countersink section used by the existing semantic parser.
    rectangle(280, 414, 325, 420); rectangle(333, 414, 376, 420)
    line(319, 414, 325, 420, 0, .6); line(339, 414, 333, 420, 0, .6)
    line(325, 383, 325, 420); line(333, 383, 333, 420)
    line(319, 391, 339, 391); line(319, 385, 319, 414); line(339, 385, 339, 414)
    text(329, 387, f"Ø{label(values['countersink_diameter'])}", 10)
    line(325, 402, 363, 402); line(333, 399, 333, 406)
    text(370, 404, f"Ø{label(values['hole_diameter'])}", 10)
    text(329, 372, f"{label(values['countersink_angle'])}°", 11)
    text(328, 470, 'COUNTERSINK SECTION', 8)
    c.showPage(); c.save()
    return path, values
