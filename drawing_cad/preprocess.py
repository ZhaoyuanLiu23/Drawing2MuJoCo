import cv2
import numpy as np


def preprocess(page):
    image = page.image
    h, w = image.shape[:2]
    if max(h, w) > 2800:
        factor = 2800 / max(h, w)
        page.image = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        for text in page.texts:
            text["bbox"] = [v * factor for v in text["bbox"]]
        for path in page.paths:
            path["points"] *= factor
        for segment in page.segments:
            for key in ("a", "b"):
                segment[key] = [v * factor for v in segment[key]]
        page.preprocessing.append(dict(operation="resize", factor=factor))
    gray = cv2.cvtColor(page.image, cv2.COLOR_RGB2GRAY)
    # Raster deskew is intentionally limited to small scan rotations.
    if not page.paths:
        edges = cv2.Canny(gray, 70, 160)
        lines = cv2.HoughLinesP(edges, 1, np.pi/1800, 90,
                                minLineLength=max(gray.shape)*0.15, maxLineGap=12)
        angles = []
        if lines is not None:
            for x0, y0, x1, y1 in lines[:, 0]:
                angle = np.degrees(np.arctan2(y1-y0, x1-x0))
                angle = (angle + 45) % 90 - 45
                if abs(angle) <= 5:
                    angles.append(angle)
        skew = float(np.median(angles)) if angles else 0.0
        if 0.15 < abs(skew) <= 5:
            h, w = gray.shape
            matrix = cv2.getRotationMatrix2D((w/2, h/2), skew, 1.0)
            page.image = cv2.warpAffine(page.image, matrix, (w, h), borderValue=(255,255,255))
            gray = cv2.cvtColor(page.image, cv2.COLOR_RGB2GRAY)
            page.preprocessing.append(dict(operation="deskew", degrees=skew,
                                           confidence=0.8, inferred=True))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    page.preprocessing.append(dict(operation="grayscale_otsu", foreground="dark"))
    return gray, binary
