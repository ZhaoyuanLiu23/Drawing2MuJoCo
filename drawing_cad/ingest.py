"""PDF vectors/text and bitmap readers. All evidence uses top-left pixel coordinates."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
import pymupdf

from .types import DrawingPage


def _point(p, scale):
    return np.asarray([p.x * scale, p.y * scale], dtype=float)


class PDFAdapter:
    def read(self, path, page=1, dpi=180):
        with pymupdf.open(path) as pdf:
            if not 1 <= page <= len(pdf):
                raise ValueError(f"Page {page} does not exist (document has {len(pdf)} pages).")
            source = pdf[page - 1]
            rotation = source.rotation
            source.set_rotation(0)  # normalize coordinates and render consistently, in memory only
            scale = dpi / 72
            pixels = source.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = np.frombuffer(pixels.samples, dtype=np.uint8).reshape(pixels.height, pixels.width, 3).copy()
            result = DrawingPage(image, page, len(pdf))
            result.preprocessing.append(dict(operation="pdf_render", dpi=dpi, original_rotation=rotation))
            for word in source.get_text("words", sort=True):
                x0, y0, x1, y1, text, *_ = word
                result.texts.append(dict(id=f"text_{len(result.texts)}", text=text,
                                         bbox=[v * scale for v in (x0, y0, x1, y1)],
                                         confidence=1.0, source="pdf_text"))
            for drawing in source.get_drawings():
                if drawing["type"] == "f":
                    continue  # filled arrowheads are not profile boundaries
                chunk = []

                def flush():
                    nonlocal chunk
                    if len(chunk) >= 2:
                        points = np.asarray(chunk)
                        result.paths.append(dict(id=f"path_{len(result.paths)}", points=points,
                                                 closed=bool(np.linalg.norm(points[0] - points[-1]) < 1.5),
                                                 source="pdf_vector"))
                    chunk = []

                for item in drawing["items"]:
                    if item[0] == "l":
                        points = [_point(item[1], scale), _point(item[2], scale)]
                    elif item[0] == "c":
                        a, b, c, d = [_point(p, scale) for p in item[1:]]
                        points = [(1-t)**3*a + 3*(1-t)**2*t*b + 3*(1-t)*t*t*c + t**3*d
                                  for t in np.linspace(0, 1, 17)]
                    elif item[0] == "re":
                        r = item[1]
                        points = [np.array(v)*scale for v in ((r.x0,r.y0),(r.x1,r.y0),
                                  (r.x1,r.y1),(r.x0,r.y1),(r.x0,r.y0))]
                    else:
                        flush()
                        continue
                    if chunk and np.linalg.norm(chunk[-1] - points[0]) > 1.5:
                        flush()
                    chunk.extend(points if not chunk else points[1:])
                    for start, end in zip(points[:-1], points[1:]):
                        result.segments.append(dict(id=f"line_{len(result.segments)}", a=start.tolist(),
                                                    b=end.tolist(), source="pdf_vector"))
                    if len(chunk) > 3 and np.linalg.norm(chunk[-1] - chunk[0]) < 1.5:
                        flush()
                if drawing.get("closePath") and chunk:
                    chunk.append(chunk[0])
                flush()
            if len(pdf) > 1:
                result.warnings.append(f"Only page {page}/{len(pdf)} is processed; use --page to select another page.")
            return result


class RasterAdapter:
    def read(self, path, page=1, dpi=180):
        if page != 1:
            raise ValueError("Raster inputs have only one page.")
        with Image.open(path) as source:
            source = ImageOps.exif_transpose(source)
            rgba = source.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            image = np.asarray(Image.alpha_composite(white, rgba).convert("RGB")).copy()
        return DrawingPage(image, 1, 1, preprocessing=[dict(operation="exif_and_alpha_normalization")])


ADAPTERS = {".pdf": PDFAdapter(), ".png": RasterAdapter(), ".jpg": RasterAdapter(), ".jpeg": RasterAdapter()}


def read_drawing(path: Path, page=1, dpi=180):
    if not path.is_file():
        raise FileNotFoundError(f"Drawing not found: {path}")
    if path.suffix.lower() not in ADAPTERS:
        raise ValueError("Unsupported format. V0.1 accepts PDF, PNG and JPG; DXF/DWG adapters are reserved.")
    return ADAPTERS[path.suffix.lower()].read(path, page, dpi)
