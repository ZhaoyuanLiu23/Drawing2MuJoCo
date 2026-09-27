"""File-bound adapter to the existing Drawing2CAD CLI; no recognition logic."""

import json
import os
from pathlib import Path
import subprocess

from app import settings
from app.errors import UploadError
from app.services.upload_storage import JOB_ID_PATTERN, _root

ARTIFACTS = {"model.stl": "model/stl", "model.step": "application/step", "parsed.json": "application/json"}


def response(job_id, status="error", *, code=None, message=None, parsed=False, pipeline_status=None):
    base = f"/api/jobs/{job_id}/cad"
    generated = status == "generated"
    return {
        "status": status,
        "job_id": job_id,
        "stl_url": f"{base}/model.stl" if generated else None,
        "step_url": f"{base}/model.step" if generated else None,
        "parsed_json_url": f"{base}/parsed.json" if parsed else None,
        "pipeline_status": pipeline_status,
        "error": {"code": code, "message": message} if code else None,
    }


def _local(path: Path) -> Path:
    if path.resolve() != path.absolute():
        raise UploadError(400, "unsafe_job_path", "The job contains a redirected filesystem path.")
    return path


def _read_json(path):
    return json.loads(_local(path).read_text(encoding="utf-8"))


def job_input(job_id):
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise UploadError(400, "invalid_job_id", "Invalid job_id.")
    directory = _local(_root() / job_id)
    if not directory.is_dir() or not _local(directory / "job.json").is_file():
        raise UploadError(404, "job_not_found", "Upload a drawing before generating CAD.")
    try:
        metadata = _read_json(directory / "job.json")
        relative = metadata.get("input_file")
        allowed = {f"input/drawing{extension}" for extension in settings.FILE_TYPES}
        if metadata.get("job_id") != job_id or relative not in allowed:
            raise ValueError("invalid metadata")
        source = _local(directory / relative)
        if not source.is_file():
            raise ValueError("missing input")
    except (ValueError, OSError, AttributeError, TypeError) as exc:
        raise UploadError(400, "invalid_job_input", "The job's stored input is missing or invalid.") from exc
    return directory, source


def _write_result(directory, result):
    temporary = _local(directory / "cad_result.json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(_local(directory / "cad_result.json"))


def run_cli(source, output):
    """Use an argument list, no shell and no client-provided options or paths."""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["MPLBACKEND"] = "Agg"
    command = [str(settings.CAD_PYTHON), str(settings.PROJECT_ROOT / "drawing2cad.py"), str(source), "--output", str(output)]
    with _local(output.parent / "cad.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command, cwd=settings.PROJECT_ROOT, env=environment,
            stdout=log, stderr=subprocess.STDOUT, shell=False,
            timeout=settings.CAD_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    return completed.returncode


def generate(job_id):
    directory, source = job_input(job_id)
    cad = _local(directory / "cad")
    lock = _local(directory / ".cad-generation.lock")
    try:
        with lock.open("x", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        return response(job_id, code="cad_in_progress", message="CAD generation is already running for this job."), 409
    except OSError as exc:
        raise UploadError(500, "cad_storage_failed", "The CAD job lock could not be created.") from exc

    try:
        # Uploads are immutable: a completed successful job can be read again without re-exporting.
        result_file = _local(directory / "cad_result.json")
        if result_file.is_file():
            previous = _read_json(result_file)
            if previous.get("status") == "generated" and all(_local(cad / name).is_file() and (cad / name).stat().st_size for name in ARTIFACTS):
                return response(job_id, "generated", parsed=True, pipeline_status=previous.get("pipeline_status")), 200

        _write_result(directory, response(job_id, "generating"))
        if not settings.CAD_PYTHON.is_file():
            result, http_status = response(job_id, code="cad_environment_missing", message="The isolated Drawing2CAD Python environment is not installed."), 503
        else:
            if cad.exists():
                for item in cad.iterdir():
                    _local(item)
                    if not item.is_file():
                        raise UploadError(400, "unsafe_cad_directory", "The CAD output directory contains unexpected entries.")
                if any(cad.iterdir()) and not (cad / ".drawing2cad-output").is_file():
                    raise UploadError(400, "unowned_cad_directory", "The CAD output directory is not owned by Drawing2CAD.")
            cad.mkdir(exist_ok=True)
            # Invalidate only exposed artifacts before a retry; never serve a previous partial run.
            for name in ARTIFACTS:
                _local(cad / name).unlink(missing_ok=True)
            try:
                exit_code = run_cli(source, cad)
            except subprocess.TimeoutExpired:
                result, http_status = response(job_id, code="cad_timeout", message="Drawing2CAD exceeded the 180 second time limit."), 504
            else:
                report_path = _local(cad / "parsed.json")
                report = _read_json(report_path) if report_path.is_file() else {}
                pipeline_status = report.get("status")
                parsed = report_path.is_file()
                if pipeline_status == "needs_review":
                    reasons = report.get("blocking_reasons") or ["The drawing requires review before a model can be generated."]
                    result = response(job_id, "needs_review", code="needs_review", message="; ".join(str(reason) for reason in reasons), parsed=parsed, pipeline_status=pipeline_status)
                    http_status = 422
                elif exit_code == 0 and pipeline_status == "generated_with_assumptions" and all(_local(cad / name).is_file() and (cad / name).stat().st_size for name in ARTIFACTS):
                    result, http_status = response(job_id, "generated", parsed=True, pipeline_status=pipeline_status), 200
                else:
                    reason = (report.get("error") or {}).get("message") or "Drawing2CAD did not produce a complete, successful CAD export. See the job's cad.log."
                    result, http_status = response(job_id, code="cad_failed", message=str(reason), parsed=parsed, pipeline_status=pipeline_status), 422
        _write_result(directory, result)
        return result, http_status
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = response(job_id, code="cad_failed", message="CAD generation failed or returned invalid output. Check the job's cad.log.")
        try:
            _write_result(directory, result)
        except OSError:
            pass
        return result, 500
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError as exc:
            raise UploadError(500, "cad_lock_cleanup_failed", "The CAD job lock could not be removed.") from exc


def artifact_path(job_id, artifact):
    if artifact not in ARTIFACTS:
        raise UploadError(404, "artifact_not_found", "This CAD artifact is not available.")
    directory, _ = job_input(job_id)
    if _local(directory / ".cad-generation.lock").exists():
        raise UploadError(409, "cad_in_progress", "CAD generation is still running.")
    try:
        result = _read_json(directory / "cad_result.json")
        url_key = {"model.stl": "stl_url", "model.step": "step_url", "parsed.json": "parsed_json_url"}[artifact]
        allowed = result.get(url_key) == f"/api/jobs/{job_id}/cad/{artifact}"
        if artifact != "parsed.json":
            allowed = allowed and result.get("status") == "generated"
        path = _local(directory / "cad" / artifact)
        if not allowed or not path.is_file() or not path.stat().st_size:
            raise FileNotFoundError
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise UploadError(404, "artifact_not_found", "No successful export is available for this artifact.") from exc
    return path
