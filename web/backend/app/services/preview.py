"""Decode supported files to a passive PNG; no OCR, CAD or script execution."""

from io import BytesIO
import math

from PIL import Image, ImageOps
import pymupdf

from app import settings
from app.errors import UploadError


def render_preview(content: bytes, decoded_format: str) -> bytes:
    try:
        if decoded_format == "PDF":
            return _pdf_preview(content)
        return _image_preview(content, decoded_format)
    except UploadError:
        raise
    except (ValueError, RuntimeError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UploadError(422, "invalid_file", "The file is damaged or is not a supported drawing.") from exc


def _image_preview(content: bytes, expected: str) -> bytes:
    # Restrict decoder selection as well as checking the declared MIME/extension.
    with Image.open(BytesIO(content), formats=[expected]) as image:
        if image.format != expected:
            raise UploadError(415, "content_mismatch", "File contents do not match its extension and MIME type.")
        if image.width * image.height > settings.MAX_IMAGE_PIXELS:
            raise UploadError(413, "image_too_large", "Images must contain at most 20 million pixels.")
        if getattr(image, "n_frames", 1) != 1:
            raise UploadError(422, "animated_image", "Please upload a single-frame drawing.")
        image.verify()

    with Image.open(BytesIO(content), formats=[expected]) as image:
        image.load()
        oriented = ImageOps.exif_transpose(image)
        oriented.thumbnail((settings.PREVIEW_MAX_EDGE, settings.PREVIEW_MAX_EDGE), Image.Resampling.LANCZOS)
        # Copy pixels onto a new image to omit source EXIF/XMP/comments/profiles.
        pixels = oriented.convert("RGBA")
        preview = Image.new("RGB", pixels.size, "white")
        preview.paste(pixels, mask=pixels.getchannel("A"))
        output = BytesIO()
        preview.save(output, format="PNG")
        return output.getvalue()


def _pdf_preview(content: bytes) -> bytes:
    if not content.lstrip().startswith(b"%PDF-"):
        raise UploadError(415, "content_mismatch", "File contents do not match PDF format.")
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if not document.is_pdf or document.needs_pass:
            raise UploadError(422, "protected_pdf", "Password-protected PDFs cannot be previewed.")
        if document.page_count < 1:
            raise UploadError(422, "empty_pdf", "The PDF has no pages.")
        page = document.load_page(0)
        extent = max(page.rect.width, page.rect.height)
        if not math.isfinite(extent) or extent <= 0:
            raise UploadError(422, "invalid_pdf_page", "The first PDF page has invalid dimensions.")
        scale = min(2.0, settings.PREVIEW_MAX_EDGE / extent)
        # Rasterization only: do not open links, run actions, or expose the PDF to a browser viewer.
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
        return pixmap.tobytes("png")
