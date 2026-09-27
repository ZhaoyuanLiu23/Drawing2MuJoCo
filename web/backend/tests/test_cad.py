"""Two real CLI integrations plus isolated adapter/security failure tests."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from app import settings
from app.main import app
from app.services import cad_generation as cad


def request(method, url, **kwargs):
    async def send():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.request(method, url, **kwargs)
    return asyncio.run(send())


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    monkeypatch.setattr(settings, "JOBS_ROOT", root)
    return root


def upload(content=None):
    if content is None:
        image = BytesIO()
        Image.new("RGB", (320, 240), "white").save(image, format="PNG")
        filename, content, mime = "blank.png", image.getvalue(), "image/png"
    else:
        filename, mime = "uploaded-engineering-drawing.pdf", "application/pdf"
    result = request("POST", "/api/jobs", files={"file": (filename, content, mime)})
    assert result.status_code == 201
    return result.json()["job_id"]


def generate(job_id, **kwargs):
    return request("POST", f"/api/jobs/{job_id}/generate-cad", **kwargs)


def test_real_drawing_cli_and_downloads(jobs_root, monkeypatch):
    assert settings.CAD_PYTHON.is_file(), "CAD Python is required for the integration test; do not skip it."
    source = settings.PROJECT_ROOT / "examples" / "bracket" / "input.pdf"
    job_id = upload(source.read_bytes())
    generated = generate(job_id)
    assert generated.status_code == 200, generated.text
    result = generated.json()
    assert result["status"] == "generated"
    assert result["pipeline_status"] == "generated_with_assumptions"
    assert result["error"] is None
    report = request("GET", result["parsed_json_url"])
    assert report.status_code == 200
    document = report.json()
    assert document["validation"]["valid_solid"] is True
    assert document["validation"]["step_reimport_valid"] is True
    assert document["configuration"]["units_override"] is None
    assert Path(document["source"]["path"]) == jobs_root / job_id / "input" / "drawing.pdf"
    stl = request("GET", result["stl_url"])
    step = request("GET", result["step_url"])
    assert stl.status_code == step.status_code == 200
    assert stl.content == (jobs_root / job_id / "cad" / "model.stl").read_bytes()
    assert len(stl.content) == 84 + 50 * struct.unpack("<I", stl.content[80:84])[0]
    assert b"ISO-10303-21" in step.content
    for response in (stl, step, report):
        assert "attachment;" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"
    assert (jobs_root / job_id / "cad.log").is_file()
    assert not (jobs_root / job_id / ".cad-generation.lock").exists()
    monkeypatch.setattr(cad, "run_cli", lambda *_: pytest.fail("A completed immutable job should use its existing export."))
    assert generate(job_id).json() == result


def test_real_unsupported_drawing_never_returns_model(jobs_root):
    job_id = upload()
    result = generate(job_id)
    assert result.status_code == 422, result.text
    body = result.json()
    assert body["status"] in {"needs_review", "error"}
    assert body["error"]["message"]
    assert body["stl_url"] is body["step_url"] is None
    assert request("GET", body["parsed_json_url"]).status_code == 200
    assert request("GET", f"/api/jobs/{job_id}/cad/model.stl").status_code == 404


@pytest.mark.parametrize("job_id", ["bad-id", "a" * 31, "a" * 33, "C:outside"])
def test_invalid_job_id(jobs_root, job_id):
    result = generate(job_id)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "invalid_job_id"
    assert not jobs_root.exists()


def test_missing_job(jobs_root, monkeypatch):
    monkeypatch.setattr(cad, "run_cli", lambda *_: pytest.fail("Must reject before launching CAD."))
    result = generate(uuid4().hex)
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "job_not_found"


@pytest.mark.parametrize("options", [{"json": {"path": "C:/private.pdf"}}, {"params": {"output": "../outside"}}])
def test_client_paths_and_options_rejected(jobs_root, options):
    job_id = upload()
    result = generate(job_id, **options)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "unexpected_options"
    assert not (jobs_root / job_id / "cad").exists()


def test_metadata_cannot_redirect_input(jobs_root, monkeypatch):
    job_id = upload()
    meta = jobs_root / job_id / "job.json"
    data = json.loads(meta.read_text())
    data["input_file"] = "../../outside.pdf"
    meta.write_text(json.dumps(data))
    monkeypatch.setattr(cad, "run_cli", lambda *_: pytest.fail("Unsafe input must not reach CLI."))
    result = generate(job_id)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "invalid_job_input"


@pytest.mark.parametrize("name", ["model.py", "preview.html", "cad.log"])
def test_download_allowlist(jobs_root, name):
    job_id = upload()
    assert request("GET", f"/api/jobs/{job_id}/cad/{name}").status_code == 404


def test_needs_review_reason_and_no_fake_mesh(jobs_root, monkeypatch):
    def needs_review(source, output):
        (output / "parsed.json").write_text(json.dumps({"status": "needs_review", "blocking_reasons": ["Units are unknown."]}))
        (output / "model.stl").write_bytes(b"deliberate partial output, must never be served")
        return 2
    monkeypatch.setattr(cad, "run_cli", needs_review)
    job_id = upload()
    result = generate(job_id)
    assert result.status_code == 422
    body = result.json()
    assert body["status"] == "needs_review"
    assert body["error"]["message"] == "Units are unknown."
    assert body["stl_url"] is body["step_url"] is None
    assert request("GET", body["parsed_json_url"]).status_code == 200
    assert request("GET", f"/api/jobs/{job_id}/cad/model.stl").status_code == 404


def test_cli_error_preserves_reason(jobs_root, monkeypatch):
    def fail(source, output):
        (output / "parsed.json").write_text(json.dumps({"status": "error", "error": {"message": "No supported profile was found."}}))
        return 1
    monkeypatch.setattr(cad, "run_cli", fail)
    result = generate(upload())
    assert result.status_code == 422
    assert result.json()["error"]["message"] == "No supported profile was found."
    assert result.json()["stl_url"] is None


def test_incomplete_success_is_failure(jobs_root, monkeypatch):
    def incomplete(source, output):
        (output / "parsed.json").write_text(json.dumps({"status": "generated_with_assumptions"}))
        (output / "model.stl").write_bytes(b"incomplete output")
        return 0
    monkeypatch.setattr(cad, "run_cli", incomplete)
    result = generate(upload())
    assert result.status_code == 422
    assert result.json()["status"] == "error"
    assert result.json()["stl_url"] is None


def test_timeout_cleans_lock_and_blocks_partial_output(jobs_root, monkeypatch):
    def timeout(source, output):
        (output / "model.stl").write_bytes(b"partial")
        raise subprocess.TimeoutExpired("cad", 180)
    monkeypatch.setattr(cad, "run_cli", timeout)
    job_id = upload()
    result = generate(job_id)
    assert result.status_code == 504
    assert result.json()["error"]["code"] == "cad_timeout"
    assert not (jobs_root / job_id / ".cad-generation.lock").exists()
    assert request("GET", f"/api/jobs/{job_id}/cad/model.stl").status_code == 404


def test_missing_cad_environment_fails_without_fallback(jobs_root, monkeypatch):
    monkeypatch.setattr(settings, "CAD_PYTHON", jobs_root / "missing-python")
    result = generate(upload())
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "cad_environment_missing"


def test_same_job_is_not_run_concurrently(jobs_root, monkeypatch):
    job_id = upload()
    lock = jobs_root / job_id / ".cad-generation.lock"
    lock.write_text("other active process")
    monkeypatch.setattr(cad, "run_cli", lambda *_: pytest.fail("Must not start concurrently."))
    assert generate(job_id).status_code == 409
    assert lock.read_text() == "other active process"


def test_unowned_output_is_not_overwritten(jobs_root):
    job_id = upload()
    output = jobs_root / job_id / "cad"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("preserve this file")
    result = generate(job_id)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "unowned_cad_directory"
    assert existing.read_text() == "preserve this file"


def test_cli_launch_is_fixed_argument_list(jobs_root, monkeypatch):
    job_id = upload()
    directory, source = cad.job_input(job_id)
    output = directory / "cad"
    def capture(command, **kwargs):
        assert command == [str(settings.CAD_PYTHON), str(settings.PROJECT_ROOT / "drawing2cad.py"), str(source), "--output", str(output)]
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 180
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", capture)
    assert cad.run_cli(source, output) == 0
