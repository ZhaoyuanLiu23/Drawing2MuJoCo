import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.errors import UploadError
from app.services.simulation import simulate, result_path, video_path
from app.services.simulation_request import result_template

router = APIRouter()


@router.post("/api/jobs/{job_id}/simulate")
async def simulate_job(job_id: str, request: Request):
    try:
        if request.query_params:
            raise UploadError(400, "unexpected_options", "不接受查询参数或服务器路径。")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise UploadError(415, "json_required", "请使用 application/json 提交仿真参数。")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 4096:
                raise UploadError(413, "request_too_large", "仿真参数请求过大。")
        try:
            parameters = json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise UploadError(422, "invalid_json", "仿真参数必须是有效 JSON。") from exc
        result, status = await run_in_threadpool(simulate, job_id, parameters)
        return JSONResponse(result, status_code=status)
    except UploadError as exc:
        result = result_template(job_id)
        result["status"] = "rejected"
        result["error"] = dict(code=exc.code, message=exc.message)
        return JSONResponse(result, status_code=exc.status)
    except OSError:
        result = result_template(job_id)
        result["error"] = dict(code="simulation_storage_failed", message="无法保存仿真文件。")
        return JSONResponse(result, status_code=500)


@router.get("/api/jobs/{job_id}/simulation/{run_id}/result.json")
def download_result(job_id: str, run_id: str):
    return FileResponse(result_path(job_id, run_id), media_type="application/json", filename="simulation-result.json",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/api/jobs/{job_id}/simulation/{run_id}/simulation.mp4")
def download_video(job_id: str, run_id: str):
    # FileResponse supports byte ranges for HTML5 video seeking.
    return FileResponse(video_path(job_id, run_id), media_type="video/mp4", filename="simulation.mp4",
                        content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
