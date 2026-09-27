"""Real HTTP-to-robot integration plus isolated web boundary failures."""
import asyncio
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from app import settings
from app.main import app
from app.services import simulation
from app.services.cad_generation import response as cad_response
from app.services.simulation_request import result_template

PARAMETERS = dict(object_start=dict(x=.5, y=0., yaw_deg=0.), target=dict(x=.46, y=-.09))


def request(method, url, **kwargs):
    async def send():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localtest", timeout=650) as client:
            return await client.request(method, url, **kwargs)
    return asyncio.run(send())


def upload():
    image = BytesIO()
    Image.new("RGB", (32, 32), "white").save(image, format="PNG")
    response = request("POST", "/api/jobs", files={"file": ("drawing.png", image.getvalue(), "image/png")})
    assert response.status_code == 201
    return response.json()["job_id"]


@pytest.fixture
def root(tmp_path, monkeypatch):
    # Keep test paths comparable to production's fixed outputs/web/jobs root.
    # Pytest's full test-name directories exceed legacy Python's Windows limit
    # after nested scenes and content-hashed robot asset names are appended.
    monkeypatch.setattr(settings, "JOBS_ROOT", tmp_path.parent / uuid4().hex[:8])
    return settings.JOBS_ROOT


@pytest.fixture(scope="module")
def real_cad(tmp_path_factory):
    output = tmp_path_factory.mktemp("simulation_cad") / "cad"
    process = subprocess.run([str(settings.CAD_PYTHON), str(settings.PROJECT_ROOT / "drawing2cad.py"),
                              str(settings.PROJECT_ROOT / "examples/bracket/input.pdf"), "--output", str(output)],
                             capture_output=True, timeout=180)
    assert process.returncode == 0, process.stdout.decode("utf8", errors="replace")
    return output


@pytest.fixture
def cad_job(root, real_cad):
    # Boundary tests reuse this module's freshly generated CAD. The acceptance
    # test below exercises upload -> generate-cad -> simulate without this shortcut.
    pdf = settings.PROJECT_ROOT / "examples/bracket/input.pdf"
    response = request("POST", "/api/jobs", files={"file": ("part.pdf", pdf.read_bytes(), "application/pdf")})
    job_id = response.json()["job_id"]
    shutil.copytree(real_cad, root / job_id / "cad")
    (root / job_id / "cad_result.json").write_text(json.dumps(cad_response(job_id, "generated", parsed=True)), encoding="utf8")
    return job_id


def simulate(job_id, parameters=None):
    return request("POST", f"/api/jobs/{job_id}/simulate", json=parameters if parameters is not None else PARAMETERS)


def test_real_api_current_cad_to_panda_visual_verification(root):
    # The acceptance flow actually invokes upload -> CAD API -> simulation API.
    pdf = settings.PROJECT_ROOT / "examples/bracket/input.pdf"
    job_id = request("POST", "/api/jobs", files={"file": ("part.pdf", pdf.read_bytes(), "application/pdf")}).json()["job_id"]
    assert request("POST", f"/api/jobs/{job_id}/generate-cad").status_code == 200
    reply = simulate(job_id)
    assert reply.status_code == 200, reply.text
    report = reply.json()
    assert report["status"] == "succeeded"
    assert report["visual_task_success"] is True
    assert report["grasp_execution_completed"] is True
    assert report["place_execution_completed"] is True
    assert report["pose_source"] == "vision" and report["viewer"] is False
    assert report["parameters"] == PARAMETERS
    assert [s["status"] for s in report["steps"]] == ["succeeded"] * 5
    assert all(report["visual_verification"]["checks"].values())
    assert report["position_error_mm"] < 10
    assert report["orientation_error_deg"] is None
    run = root / job_id / "simulation/runs" / report["run_id"]
    for artifact in ("scene.xml", "task_scene.xml", "result.json", "agent/task_plan.json", "agent/skills/frame_5.png"):
        assert (run / artifact).is_file(), artifact
    manifest = json.loads((run / "cad2mujoco/manifest.json").read_text(encoding="utf8"))
    assert manifest["inputs"]["stl"]["sha256"] == hashlib.sha256((root / job_id / "cad/model.stl").read_bytes()).hexdigest()
    saved = request("GET", report["result_json_url"])
    assert saved.json() == report
    assert "attachment" in saved.headers["content-disposition"]
    assert not (root / job_id / "simulation/.running").exists()
    assert report["video_available"] is True, report.get("video")
    assert report["video"]["complete"] is True
    assert report["video"]["frames"] > 100
    assert report["video"]["width"] == 960 and report["video"]["height"] == 540
    assert "visual_verify" in report["video"]["stages"]
    video = request("GET", report["video_url"])
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.content == (run / "simulation.mp4").read_bytes()
    assert b"ftyp" in video.content[:32]
    partial = request("GET", report["video_url"], headers={"Range": "bytes=100-299"})
    assert partial.status_code == 206 and partial.content == video.content[100:300]


@pytest.mark.parametrize("section,key,value,reason", [("target", "x", 2., "target_zone_outside_table"),
                                                       ("object_start", "y", -2., "object_start_outside_table")])
def test_outside_positions_rejected_before_physics(root, cad_job, section, key, value, reason):
    parameters = deepcopy(PARAMETERS)
    parameters[section][key] = value
    reply = simulate(cad_job, parameters)
    assert reply.status_code == 422, reply.text
    result = reply.json()
    assert result["status"] == "rejected"
    assert result["physics_started"] is False
    assert reason in result["error"]["message"]
    run = root / cad_job / "simulation/runs" / result["run_id"]
    assert not (run / "cad2mujoco").exists()
    assert not (run / "agent").exists()
    assert result["parameters"] == parameters  # No default substitution.
    assert result["video_available"] is False and result["video_url"] is None
    assert request("GET", f"/api/jobs/{cad_job}/simulation/{result['run_id']}/simulation.mp4").status_code == 404


@pytest.mark.parametrize("job_id,code", [("nonsense", 400), ("../escape", 404), ("f" * 32, 404)])
def test_invalid_or_missing_job(root, monkeypatch, job_id, code):
    monkeypatch.setattr(simulation, "run_worker", lambda *_: pytest.fail("Must not launch a worker"))
    assert simulate(job_id).status_code == code


def test_job_without_cad(root, monkeypatch):
    monkeypatch.setattr(simulation, "run_worker", lambda *_: pytest.fail("Must not launch a worker"))
    reply = simulate(upload())
    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "cad_required"


@pytest.mark.parametrize("parameters", [ {}, {"object_start": PARAMETERS["object_start"]},
    {**PARAMETERS, "path": "C:/outside.xml"}, {**PARAMETERS, "target": dict(x="0.5", y=0)},
    {**PARAMETERS, "target": dict(x=True, y=0)}, {**PARAMETERS, "object_start": dict(x=.5, y=0)},
    {**PARAMETERS, "target": dict(x=None, y=0)}, {**PARAMETERS, "target": dict(x=0, y=0, size=1)}])
def test_bad_parameters_before_worker(root, monkeypatch, parameters):
    job_id = upload()
    monkeypatch.setattr(simulation, "run_worker", lambda *_: pytest.fail("Must not launch a worker"))
    reply = simulate(job_id, parameters)
    assert reply.status_code == 422
    assert reply.json()["error"]["code"] == "invalid_parameters"
    assert not (root / job_id / "simulation").exists()


@pytest.mark.parametrize("content,expected", [(b'{"target":', 422), (b"x" * 4097, 413),
    (b'{"object_start":{"x":0.5,"y":0,"yaw_deg":NaN},"target":{"x":0.46,"y":-0.09}}', 422)])
def test_bad_raw_json(root, monkeypatch, content, expected):
    monkeypatch.setattr(simulation, "run_worker", lambda *_: pytest.fail("Must not launch a worker"))
    reply = request("POST", f"/api/jobs/{upload()}/simulate", content=content, headers={"Content-Type": "application/json"})
    assert reply.status_code == expected


@pytest.mark.parametrize("has_video", [False, True])
def test_pipeline_failure_propagates_and_downloads(root, cad_job, monkeypatch, has_video):
    def failed(run):
        result = result_template(cad_job, run.name)
        result["error"] = dict(code="GRASP_FAILED", message="no_feasible_grasp_and_place_candidate", origin_stage="planning")
        if has_video:
            # Service-only failure/availability contract; real encoding is checked
            # by the acceptance test and robot_worker_checks failure execution.
            (run / "simulation.mp4").write_bytes(b"service contract sentinel")
            result["video_available"] = True
        (run / "result.json").write_text(json.dumps(result), encoding="utf8")
        return 1
    monkeypatch.setattr(simulation, "run_worker", failed)
    reply = simulate(cad_job)
    assert reply.status_code == 422
    assert reply.json()["error"]["code"] == "GRASP_FAILED"
    assert reply.json()["visual_task_success"] is False
    assert request("GET", reply.json()["result_json_url"]).json() == reply.json()
    assert reply.json()["status"] == "failed"
    assert reply.json()["video_available"] is has_video
    if has_video:
        assert request("GET", reply.json()["video_url"]).status_code == 200


def test_timeout_no_success_and_release_lock(root, cad_job, monkeypatch):
    def timeout(run):
        raise subprocess.TimeoutExpired("worker", 600)
    monkeypatch.setattr(simulation, "run_worker", timeout)
    reply = simulate(cad_job)
    assert reply.status_code == 504
    assert reply.json()["error"]["code"] == "SIMULATION_TIMEOUT"
    assert not (root / cad_job / "simulation/.running").exists()


def test_duplicate_run_denied(root, cad_job, monkeypatch):
    folder = root / cad_job / "simulation"
    folder.mkdir()
    (folder / ".running").touch()
    monkeypatch.setattr(simulation, "run_worker", lambda *_: pytest.fail("Duplicate execution"))
    assert simulate(cad_job).status_code == 409


def test_missing_stl_denied(root, cad_job, monkeypatch):
    (root / cad_job / "cad/model.stl").unlink()
    monkeypatch.setattr(simulation, "run_worker", lambda *_: pytest.fail("No mesh"))
    assert simulate(cad_job).json()["error"]["code"] == "cad_required"


def test_robot_worker_guards_and_changed_pose(root, cad_job):
    # Run invariant checks in the existing MuJoCo environment; missing runtime fails.
    process = subprocess.run([str(settings.SIMULATION_PYTHON), str(Path(__file__).with_name("robot_worker_checks.py")),
                              str(root / cad_job), str(settings.PANDA_SCENE)], capture_output=True, timeout=600)
    assert process.returncode == 0, (process.stdout + process.stderr).decode("utf8", errors="replace")


def test_no_arbitrary_result_path(root):
    job_id = upload()
    assert request("GET", f"/api/jobs/{job_id}/simulation/nope/result.json").status_code == 400
    assert request("GET", f"/api/jobs/{job_id}/simulation/{uuid4().hex}/scene.xml").status_code == 404


@pytest.mark.parametrize("job,run,code", [("invalid", "a" * 32, 400), ("f" * 32, "a" * 32, 404),
    (None, "invalid", 400), (None, "a" * 32, 404), (None, "..%2F..", 404)])
def test_video_rejects_invalid_job_or_run(root, job, run, code):
    assert request("GET", f"/api/jobs/{job or upload()}/simulation/{run}/simulation.mp4").status_code == code


def test_unpublished_partial_or_redirected_video_not_served(root):
    job_id, run_id = upload(), uuid4().hex
    run = root / job_id / "simulation/runs" / run_id
    run.mkdir(parents=True)
    result = result_template(job_id, run_id)
    result["result_json_url"] = f"/api/jobs/{job_id}/simulation/{run_id}/result.json"
    (run / "result.json").write_text(json.dumps(result), encoding="utf8")
    (run / "simulation.mp4").write_bytes(b"not authorized")
    (run / "simulation.partial.mp4").write_bytes(b"unfinished")
    url = f"/api/jobs/{job_id}/simulation/{run_id}"
    assert request("GET", url + "/simulation.mp4").status_code == 404
    assert request("GET", url + "/simulation.partial.mp4").status_code == 404
    assert request("GET", url + "/../request.json").status_code == 404
    result.update(video_available=True, video_url="file:///outside.mp4")
    (run / "result.json").write_text(json.dumps(result), encoding="utf8")
    assert request("GET", url + "/simulation.mp4").status_code == 404
