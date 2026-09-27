"""Real multipart uploads, decoding, disk storage and preview HTTP responses."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pymupdf
import pytest

from app import settings
from app.main import app


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    monkeypatch.setattr(settings, "JOBS_ROOT", root)
    return root


def request(method, url, **kwargs):
    async def send():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


def raster(fmt="PNG", color=(20, 170, 80)):
    output = BytesIO()
    Image.new("RGB", (96, 64), color).save(output, format=fmt)
    return output.getvalue()


def pdf(encrypted=False):
    with pymupdf.open() as document:
        for color in [(1, 0, 0), (0, 0, 1)]:
            page = document.new_page(width=180, height=120)
            page.draw_rect(page.rect, color=color, fill=color)
        if encrypted:
            return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
        return document.tobytes()


def upload(filename="drawing.png", content=None, mime="image/png"):
    return request("POST", "/api/jobs", files={"file": (filename, raster() if content is None else content, mime)})


def assert_rejected(response, jobs_root, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]
    assert not jobs_root.exists() or not list(jobs_root.iterdir())


@pytest.mark.parametrize("filename,fmt,mime,file_type", [
    ("工程图.png", "PNG", "image/png", "png"),
    ("drawing.jpg", "JPEG", "image/jpeg", "jpeg"),
    ("drawing.jpeg", "JPEG", "image/jpeg", "jpeg"),
    ("DRAWING.PNG", "PNG", "image/png", "png"),
])
def test_raster_upload_storage_and_preview(jobs_root, filename, fmt, mime, file_type):
    content = raster(fmt)
    response = upload(filename, content, mime)
    assert response.status_code == 201, response.text
    result = response.json()
    job_id = result["job_id"]
    assert UUID(hex=job_id).version == 4
    assert len(job_id) == 32
    assert result == {
        "job_id": job_id,
        "original_filename": filename,
        "file_type": file_type,
        "preview_url": f"/api/jobs/{job_id}/preview",
    }
    directory = jobs_root / job_id
    stored = directory / "input" / f"drawing{Path(filename).suffix.lower()}"
    assert stored.resolve().is_relative_to(jobs_root.resolve())
    assert stored.read_bytes() == content
    metadata = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    assert metadata["original_filename"] == filename
    assert metadata["size_bytes"] == len(content)
    assert metadata["input_file"] == f"input/{stored.name}"
    preview = request("GET", result["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(preview.content)) as image:
        assert image.format == "PNG"
        assert image.size == (96, 64)
        actual = image.getpixel((48, 32))
        assert all(abs(a - b) < 5 for a, b in zip(actual, (20, 170, 80)))


def test_pdf_upload_renders_first_page_only(jobs_root):
    content = pdf()
    response = upload("two-pages.pdf", content, "application/pdf")
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["file_type"] == "pdf"
    assert (jobs_root / result["job_id"] / "input" / "drawing.pdf").read_bytes() == content
    preview = request("GET", result["preview_url"])
    assert preview.status_code == 200
    with Image.open(BytesIO(preview.content)) as image:
        # The second page is blue. A red first-page pixel proves which page was rendered.
        assert image.getpixel((image.width // 2, image.height // 2)) == (255, 0, 0)
        assert max(image.size) <= settings.PREVIEW_MAX_EDGE


def test_uploading_same_name_creates_independent_jobs(jobs_root):
    red, blue = raster(color=(255, 0, 0)), raster(color=(0, 0, 255))
    first, second = upload(content=red).json(), upload(content=blue).json()
    assert first["job_id"] != second["job_id"]
    assert (jobs_root / first["job_id"] / "input" / "drawing.png").read_bytes() == red
    assert (jobs_root / second["job_id"] / "input" / "drawing.png").read_bytes() == blue
    assert len(list(jobs_root.iterdir())) == 2


@pytest.mark.parametrize("filename", ["drawing.svg", "drawing.py", "drawing.txt", "drawing.png.exe"])
def test_unsupported_extension(jobs_root, filename):
    assert_rejected(upload(filename), jobs_root, 415, "unsupported_extension")


@pytest.mark.parametrize("mime", ["application/pdf", "image/jpeg", "application/octet-stream"])
def test_declared_mime_mismatch(jobs_root, mime):
    assert_rejected(upload(mime=mime), jobs_root, 415, "mime_mismatch")


@pytest.mark.parametrize("filename,content,mime,status,code", [
    ("drawing.png", b"<html>not an image</html>", "image/png", 422, "invalid_file"),
    ("drawing.jpg", raster(), "image/jpeg", 422, "invalid_file"),
    ("drawing.pdf", raster(), "application/pdf", 415, "content_mismatch"),
    ("drawing.pdf", b"%PDF-1.7\nbroken", "application/pdf", 422, "invalid_file"),
    ("drawing.png", raster()[:50], "image/png", 422, "invalid_file"),
])
def test_corrupt_or_mislabeled_content(jobs_root, filename, content, mime, status, code):
    assert_rejected(upload(filename, content, mime), jobs_root, status, code)


@pytest.mark.parametrize("filename", ["../escape.png", "folder/escape.png", "..\\escape.png", "drawing:stream.png"])
def test_path_traversal_and_windows_paths_rejected(jobs_root, filename):
    assert_rejected(upload(filename), jobs_root, 400, "invalid_filename")
    assert not (jobs_root.parent / "escape.png").exists()


def test_multipart_normalized_windows_filename_still_uses_server_path(jobs_root):
    # python-multipart normalizes legacy browser drive-qualified filenames to a basename.
    response = upload("C:\\outside\\escape.png")
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["original_filename"] == "escape.png"
    saved = jobs_root / result["job_id"] / "input"
    assert [path.name for path in saved.iterdir()] == ["drawing.png"]
    assert (saved / "drawing.png").read_bytes() == raster()


def test_empty_file(jobs_root):
    assert_rejected(upload(content=b""), jobs_root, 422, "empty_file")


def test_password_protected_pdf(jobs_root):
    assert_rejected(upload("locked.pdf", pdf(encrypted=True), "application/pdf"), jobs_root, 422, "protected_pdf")


def test_pixel_limit_before_decoding(jobs_root, monkeypatch):
    monkeypatch.setattr(settings, "MAX_IMAGE_PIXELS", 1000)
    assert_rejected(upload(), jobs_root, 413, "image_too_large")


def test_file_size_limit(jobs_root):
    content = raster()
    content += b"\0" * (settings.MAX_UPLOAD_BYTES + 1 - len(content))
    assert_rejected(upload(content=content), jobs_root, 413, "file_too_large")


def test_exact_file_limit_accepts_multipart_overhead(jobs_root):
    content = raster()
    content += b"\0" * (settings.MAX_UPLOAD_BYTES - len(content))
    response = upload(content=content)
    assert response.status_code == 201, response.text
    assert (jobs_root / response.json()["job_id"] / "input" / "drawing.png").stat().st_size == settings.MAX_UPLOAD_BYTES


def test_oversized_request_rejected_before_parsing(jobs_root):
    assert_rejected(upload(content=b"x" * (settings.MAX_REQUEST_BYTES + 1)), jobs_root, 413, "file_too_large")


def test_chunked_request_cannot_bypass_size_limit(jobs_root, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1024)
    monkeypatch.setattr(settings, "MAX_REQUEST_BYTES", 2048)

    async def send():
        async def chunks():
            yield b'--upload\r\nContent-Disposition: form-data; name="file"; filename="drawing.png"\r\nContent-Type: image/png\r\n\r\n'
            for _ in range(6):
                yield b"x" * 512
            yield b"\r\n--upload--\r\n"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.post("/api/jobs", content=chunks(), headers={"content-type": "multipart/form-data; boundary=upload"})

    assert_rejected(asyncio.run(send()), jobs_root, 413, "file_too_large")


def test_non_multipart_request(jobs_root):
    assert_rejected(request("POST", "/api/jobs", json={"file": "x.png"}), jobs_root, 415, "multipart_required")


def test_missing_file_field(jobs_root):
    response = request("POST", "/api/jobs", files={"other": ("a.png", raster(), "image/png")})
    assert_rejected(response, jobs_root, 400, "file_required")


def test_multiple_files_rejected(jobs_root):
    response = request("POST", "/api/jobs", files=[("file", ("a.png", raster(), "image/png")), ("file", ("b.png", raster(), "image/png"))])
    assert_rejected(response, jobs_root, 400, "invalid_multipart")


def test_malformed_multipart(jobs_root):
    response = request("POST", "/api/jobs", content=b"broken", headers={"content-type": "multipart/form-data"})
    assert_rejected(response, jobs_root, 400, "invalid_multipart")


@pytest.mark.parametrize("job_id", ["not-a-job", "C:private", "0" * 32])
def test_invalid_or_unknown_preview_job(jobs_root, job_id):
    response = request("GET", f"/api/jobs/{job_id}/preview")
    assert_rejected(response, jobs_root, 404, "preview_not_found")


def test_storage_failure_is_json_and_cleans_partial_job(jobs_root, monkeypatch):
    def fail_write(path, data):
        raise OSError("simulated unavailable disk")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    assert_rejected(upload(), jobs_root, 500, "storage_failed")


def test_unfinished_job_preview_is_not_served(jobs_root):
    directory = jobs_root / ("1" * 32)
    directory.mkdir(parents=True)
    (directory / "preview.png").write_bytes(raster())
    response = request("GET", f"/api/jobs/{directory.name}/preview")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "preview_not_found"


def test_failed_cleanup_still_returns_json_error(jobs_root, monkeypatch):
    def fail_write(path, data):
        raise OSError("unavailable disk")

    def fail_unlink(path, **kwargs):
        raise OSError("cleanup denied")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    response = upload()
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "storage_failed"
    for directory in jobs_root.iterdir():
        assert request("GET", f"/api/jobs/{directory.name}/preview").status_code == 404


def test_default_storage_location_is_repository_outputs():
    assert settings.JOBS_ROOT == Path(__file__).resolve().parents[3] / "outputs" / "web" / "jobs"
