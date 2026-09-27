"""Generate and retrieve only the current job's allowlisted CAD artifacts."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.errors import UploadError
from app.services import cad_generation

router = APIRouter()


@router.post("/api/jobs/{job_id}/generate-cad")
async def generate_cad(job_id: str, request: Request):
    try:
        if request.query_params:
            raise UploadError(400, "unexpected_options", "Only job_id is accepted; server paths and generation options cannot be supplied.")
        async for chunk in request.stream():
            if chunk:
                raise UploadError(400, "unexpected_options", "This endpoint accepts no request body or server paths.")
        result, status_code = await run_in_threadpool(cad_generation.generate, job_id)
        return JSONResponse(result, status_code=status_code)
    except UploadError as exc:
        return JSONResponse(cad_generation.response(job_id, code=exc.code, message=exc.message), status_code=exc.status)


@router.get("/api/jobs/{job_id}/cad/{artifact}")
def get_cad_artifact(job_id: str, artifact: str):
    return FileResponse(
        cad_generation.artifact_path(job_id, artifact),
        media_type=cad_generation.ARTIFACTS[artifact], filename=artifact,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )
