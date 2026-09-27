"""Server-selected paths only; uploaded filenames are metadata, never paths."""

import json
from pathlib import Path
import re
from uuid import uuid4

from app import settings
from app.errors import UploadError

JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _root() -> Path:
    root = settings.JOBS_ROOT.absolute()
    # Refuse symlinks/junctions that redirect outputs/web/jobs outside its literal location.
    if root.resolve() != root:
        raise UploadError(500, "unsafe_storage", "The upload storage directory is not available.")
    return root


def save_job(content: bytes, preview: bytes, filename: str, extension: str) -> dict[str, str]:
    root = _root()
    job_dir = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        job_id = uuid4().hex
        candidate = root / job_id
        candidate.mkdir()  # exclusive allocation; never reuse an existing job
        job_dir = candidate
        if job_dir.resolve().parent != root:
            raise UploadError(500, "unsafe_storage", "The upload storage directory is not available.")
        input_dir = job_dir / "input"
        input_dir.mkdir()
        input_file = input_dir / f"drawing{extension}"
        result = {
            "job_id": job_id,
            "original_filename": filename,
            "file_type": settings.FILE_TYPES[extension][0],
            "preview_url": f"/api/jobs/{job_id}/preview",
        }
        input_file.write_bytes(content)
        (job_dir / "preview.png").write_bytes(preview)
        metadata_pending = job_dir / "job.json.tmp"
        metadata_pending.write_text(
            json.dumps({**result, "input_file": f"input/{input_file.name}", "size_bytes": len(content)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        metadata_pending.replace(job_dir / "job.json")
        return result
    except OSError as exc:
        if job_dir is not None and job_dir.resolve().parent == root:
            # Only remove the exact files created in this newly allocated job.
            try:
                for target in [job_dir / "input" / f"drawing{extension}", job_dir / "preview.png", job_dir / "job.json.tmp", job_dir / "job.json"]:
                    target.unlink(missing_ok=True)
                if (job_dir / "input").is_dir():
                    (job_dir / "input").rmdir()
                job_dir.rmdir()
            except OSError:
                # Preserve a structured failure even when the disk also refuses cleanup.
                pass
        raise UploadError(500, "storage_failed", "The server could not save the drawing. Please try again.") from exc


def preview_path(job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise UploadError(404, "preview_not_found", "The drawing preview was not found.")
    root = _root()
    job_dir = root / job_id
    preview = job_dir / "preview.png"
    metadata = job_dir / "job.json"
    if job_dir.resolve() != job_dir or preview.resolve() != preview or metadata.resolve() != metadata or not metadata.is_file() or not preview.is_file():
        raise UploadError(404, "preview_not_found", "The drawing preview was not found.")
    return preview
