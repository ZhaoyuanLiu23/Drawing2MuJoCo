"""Upload a single drawing and serve its raster preview."""

from pathlib import PurePosixPath

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app import settings
from app.errors import UploadError
from app.services.preview import render_preview
from app.services.upload_storage import preview_path, save_job

router = APIRouter()


def _validate_filename(filename: str | None, content_type: str | None) -> tuple[str, str]:
    if not filename or len(filename) > 255 or any(ord(char) < 32 or char in '/\\:' for char in filename):
        raise UploadError(400, "invalid_filename", "Please use a filename without directories or control characters.")
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in settings.FILE_TYPES:
        raise UploadError(415, "unsupported_extension", "Only PDF, PNG, JPG and JPEG files are allowed.")
    if (content_type or "").split(";", 1)[0].strip().lower() != settings.FILE_TYPES[extension][1]:
        raise UploadError(415, "mime_mismatch", "The file MIME type does not match its extension.")
    return filename, extension


@router.post("/api/jobs", status_code=201)
async def create_job(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise UploadError(415, "multipart_required", "Send one drawing as multipart/form-data in the file field.")
    if "content-length" in request.headers:
        try:
            length = int(request.headers["content-length"])
            if length < 0:
                raise ValueError
        except ValueError as exc:
            raise UploadError(400, "invalid_content_length", "Invalid upload request length.") from exc
        if length > settings.MAX_REQUEST_BYTES:
            raise UploadError(413, "file_too_large", "The maximum file size is 10 MiB.")

    received = 0

    async def limited_receive():
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > settings.MAX_REQUEST_BYTES:
                raise UploadError(413, "file_too_large", "The maximum file size is 10 MiB.")
        return message

    # Limit bytes while multipart is being parsed, including chunked requests.
    limited = Request(request.scope, receive=limited_receive)
    try:
        async with limited.form(max_files=1, max_fields=0, max_part_size=1024) as form:
            file = form.get("file")
            if len(form) != 1 or not isinstance(file, UploadFile):
                raise UploadError(400, "file_required", "Select exactly one drawing in the file field.")
            filename, extension = _validate_filename(file.filename, file.content_type)
            content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
            if len(content) > settings.MAX_UPLOAD_BYTES:
                raise UploadError(413, "file_too_large", "The maximum file size is 10 MiB.")
            if not content:
                raise UploadError(422, "empty_file", "The drawing file is empty.")
            # Rendering stays on this process's event-loop thread. PyMuPDF is never
            # called concurrently from a thread pool; there are no core pipeline imports.
            preview = render_preview(content, settings.FILE_TYPES[extension][2])
            return save_job(content, preview, filename, extension)
    except HTTPException as exc:
        raise UploadError(400, "invalid_multipart", "Upload one file using a valid multipart request.") from exc


@router.get("/api/jobs/{job_id}/preview")
def get_preview(job_id: str) -> FileResponse:
    return FileResponse(
        preview_path(job_id),
        media_type="image/png",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"},
    )
