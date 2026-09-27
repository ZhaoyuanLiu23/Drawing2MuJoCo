"""Job-confined, isolated headless robot invocation and immutable result downloads."""
import json
import os
import subprocess
from uuid import uuid4

from app import settings
from app.errors import UploadError
from app.services.cad_generation import job_input, artifact_path, _local, _read_json
from app.services.upload_storage import JOB_ID_PATTERN
from app.services.simulation_request import validate_parameters, result_template


def write_json(path, value):
    path = _local(path)
    pending = _local(path.with_suffix(".tmp"))
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    pending.replace(path)


def run_worker(run):
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    with (run / "worker.log").open("w", encoding="utf8") as log:
        return subprocess.run(
            [str(settings.SIMULATION_PYTHON), str(settings.PROJECT_ROOT / "web/backend/simulation_worker.py"), str(run)],
            cwd=settings.PROJECT_ROOT, env=environment, shell=False, stdout=log, stderr=subprocess.STDOUT,
            timeout=settings.SIMULATION_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).returncode


def simulate(job_id, parameters):
    directory, _ = job_input(job_id)
    try:
        validate_parameters(parameters)
    except ValueError as exc:
        raise UploadError(422, "invalid_parameters", str(exc)) from exc
    try:
        for name in ("model.stl", "parsed.json"):
            artifact_path(job_id, name)
        if _read_json(directory / "cad_result.json").get("status") != "generated":
            raise ValueError("CAD 未成功生成。")
    except (UploadError, OSError, ValueError) as exc:
        raise UploadError(409, "cad_required", "请先成功生成当前图纸的 CAD，且 STL/解析 JSON 必须存在。") from exc
    if not settings.SIMULATION_PYTHON.is_file() or not settings.PANDA_SCENE.is_file():
        raise UploadError(503, "simulation_environment_missing", "缺少 MuJoCo Python 或 Panda 场景；请检查服务端运行环境。")
    simulation = _local(directory / "simulation")
    simulation.mkdir(exist_ok=True)
    lock = _local(simulation / ".running")
    try:
        with lock.open("x", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError as exc:
        raise UploadError(409, "simulation_in_progress", "当前图纸正在仿真，请等待完成。") from exc
    try:
        runs = _local(simulation / "runs")
        runs.mkdir(exist_ok=True)
        run_id = uuid4().hex
        run = _local(runs / run_id)
        run.mkdir()
        write_json(run / "request.json", dict(parameters=parameters, profile=dict(
            panda_scene=str(settings.PANDA_SCENE.resolve()), density_kg_m3=settings.SIMULATION_DENSITY_KG_M3,
            density_source="server test configuration; not measured or inferred material",
            zone_size_m=list(settings.SIMULATION_ZONE_SIZE_M), frame="world", length_unit="m", yaw_unit="deg")))
        pending = result_template(job_id, run_id)
        pending["status"] = "running"
        write_json(simulation / "result.json", pending)
        try:
            exit_code = run_worker(run)
            result = _read_json(run / "result.json")
            if result.get("job_id") != job_id or result.get("run_id") != run_id or result.get("status") not in {"succeeded", "failed", "rejected"}:
                raise ValueError("无效的仿真结果。")
            if result["status"] == "succeeded" and (exit_code != 0 or result.get("visual_task_success") is not True):
                raise ValueError("仿真没有返回完整成功证据。")
            status = 200 if result["status"] == "succeeded" else 422
        except subprocess.TimeoutExpired:
            result, status = result_template(job_id, run_id), 504
            result["error"] = dict(code="SIMULATION_TIMEOUT", message="仿真超过 600 秒限制，工作进程已停止。")
        except (OSError, ValueError, TypeError, AttributeError):
            result, status = result_template(job_id, run_id), 500
            result["error"] = dict(code="SIMULATION_WORKER_FAILED", message="仿真进程失败或返回无效结果，请检查本次 worker.log。")
        result["result_json_url"] = f"/api/jobs/{job_id}/simulation/{run_id}/result.json"
        # Publish only this run's finalized video, never a partial or an older run.
        video = _local(run / "simulation.mp4")
        result["video_available"] = result.get("video_available") is True and video.is_file() and video.stat().st_size > 0
        result["video_url"] = f"/api/jobs/{job_id}/simulation/{run_id}/simulation.mp4" if result["video_available"] else None
        write_json(run / "result.json", result)
        write_json(simulation / "result.json", result)
        return result, status
    finally:
        lock.unlink(missing_ok=True)


def result_path(job_id, run_id):
    directory, _ = job_input(job_id)
    if not JOB_ID_PATTERN.fullmatch(run_id):
        raise UploadError(400, "invalid_run_id", "无效的仿真运行编号。")
    path = _local(directory / "simulation" / "runs" / run_id / "result.json")
    try:
        report = _read_json(path)
        if report.get("result_json_url") != f"/api/jobs/{job_id}/simulation/{run_id}/result.json":
            raise ValueError
    except (OSError, ValueError, AttributeError) as exc:
        raise UploadError(404, "simulation_result_missing", "该次仿真尚无可保存的结果。") from exc
    return path


def video_path(job_id, run_id):
    report_path = result_path(job_id, run_id)
    report = _read_json(report_path)
    path = _local(report_path.parent / "simulation.mp4")
    if (report.get("video_available") is not True or
            report.get("video_url") != f"/api/jobs/{job_id}/simulation/{run_id}/simulation.mp4" or
            not path.is_file() or path.stat().st_size == 0):
        raise UploadError(404, "simulation_video_missing", "本次运行没有可用视频。")
    return path
