"""Strict release gate across two Python environments and all existing suites."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .manifest import SUITES, ACCEPTANCE, STATUSES, gate

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf8")


def probe(python, modules):
    code = "import importlib,json,sys; " + "; ".join("importlib.import_module(" + repr(m) + ")" for m in modules)
    code += "; print(json.dumps({'python':sys.executable,'version':sys.version}))"
    try:
        p = subprocess.run([str(python), "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf8", errors="replace", timeout=90)
        return dict(success=p.returncode == 0, python=str(python), stdout=p.stdout, stderr=p.stderr, returncode=p.returncode)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(success=False, python=str(python), error=str(exc))


def preflight(python, cad_python, panda):
    allowed = {path for _, path, _ in SUITES.values()}
    files = [p for p in (ROOT / "tests").rglob("test_*.py") if "__pycache__" not in p.parts]
    unlisted = [str(p.relative_to(ROOT)) for p in files if p.parent.relative_to(ROOT).as_posix() not in allowed]
    checks = {"robot_python": probe(python, ["mujoco", "numpy", "scipy", "PIL", "matplotlib", "jsonschema"]),
              "cad_python": probe(cad_python, ["pytest", "cadquery", "cv2", "fitz", "rapidocr_onnxruntime", "reportlab"]),
              "panda_scene": dict(success=bool(panda and Path(panda).is_file()), path=str(panda) if panda else None,
                                  required=True, missing_policy="fail; never turn Panda skips into release success")}
    checks["test_inventory"] = dict(success=not unlisted, files=len(files), unlisted_test_files=unlisted)
    return dict(success=all(c["success"] for c in checks.values()), checks=checks)


def snapshot():
    paths = []
    for directory in ("drawing_cad", "cad_mujoco", "manipulation", "perception", "pick_place_task", "embodied_agent", "language_planner", "tests", "validation_tools", "schemas", "scripts"):
        paths += [p for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    paths += [ROOT / p for p in ("panda_grasp.py", "pick_and_place.py", "full_validation.py")]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def run_group(name, python, cad_python, output, env, timeout):
    started = time.perf_counter()
    directory = output / name
    directory.mkdir()
    child_env = env.copy()
    for key in ("CAD_MUJOCO_TEST_REPORT", "MANIPULATION_TEST_REPORT", "PERCEPTION_TEST_REPORT", "PICK_PLACE_TEST_REPORT", "EMBODIED_AGENT_TEST_REPORT", "LANGUAGE_PLANNER_TEST_REPORT"):
        child_env[key] = str(directory / (key.lower() + ".json"))
    child_env["E2E_ARTIFACT_DIR"] = str(directory / "artifacts")
    if name in SUITES:
        kind, _, expected = SUITES[name]
        command = [str(cad_python if kind == "pytest" else python), "-m", "validation_tools.worker", name, "--report", str(directory / "tests.json")]
    else:
        script, source, options, expected = ACCEPTANCE[name]
        command = [str(cad_python), script, source, *options, "--output", str(directory / "artifacts")]
    report = dict(suite=name, collected=0, cases=[], runner_errors=[], returncode=None)
    with (directory / "run.log").open("w", encoding="utf8") as log:
        try:
            process = subprocess.run(command, cwd=ROOT, env=child_env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            if name in SUITES:
                report = json.loads((directory / "tests.json").read_text(encoding="utf8"))
            else:
                acceptance = json.loads((directory / "artifacts/acceptance_report.json").read_text(encoding="utf8"))
                report["cases"] = [dict(id=name + "/" + c["case"], status=c["status"]) for c in acceptance["cases"]]
                report["collected"] = len(report["cases"])
            report["returncode"] = process.returncode
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            report["runner_errors"].append(dict(status="error", detail=str(exc)))
    report.update(gate(report, expected))
    report.update(command=command, seconds=time.perf_counter() - started, log=str(directory / "run.log"))
    write_json(directory / "validation.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Robot/test Python with MuJoCo and jsonschema")
    parser.add_argument("--cad-python", default=str(ROOT / ".venv-drawing2cad/Scripts/python.exe"))
    parser.add_argument("--panda-scene", default=os.environ.get("CAD_MUJOCO_PANDA_SCENE"))
    parser.add_argument("--output", default=str(ROOT / "outputs/full_validation" / datetime.now().strftime("%Y%m%d_%H%M%S")))
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=1800.)
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.timeout <= 0:
        parser.error("jobs and timeout must be positive")
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new/empty output directory; stale evidence cannot be accepted")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = dict(schema_version="1.0", created_utc=datetime.now(timezone.utc).isoformat(), success=False,
                  expected_framework_tests=sum(s[2] for s in SUITES.values()), expected_acceptance_cases=sum(s[3] for s in ACCEPTANCE.values()),
                  counting_policy="one framework case per collected id; subTests are assertions, not extra passes; acceptance cases separate",
                  groups={}, release_blockers=[])
    result["preflight"] = preflight(args.python, args.cad_python, args.panda_scene)
    if not result["preflight"]["success"]:
        result["release_blockers"].append("Required environment is unavailable. Full validation did not run.")
        result["blocked_groups"] = list(SUITES) + list(ACCEPTANCE)
    else:
        before = snapshot()
        env = os.environ.copy()
        env.update(CAD_MUJOCO_PANDA_SCENE=str(Path(args.panda_scene).resolve()), CAD_TEST_PYTHON=str(Path(args.cad_python).resolve()),
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_group, name, args.python, args.cad_python, output, env, args.timeout): name for name in (*SUITES, *ACCEPTANCE)}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    group = future.result()
                except Exception as exc:
                    group = dict(success=False, cases=[], runner_errors=[dict(status="error", detail=str(exc))], counts={s: 0 for s in STATUSES})
                result["groups"][name] = group
                print(name + ": " + ("PASS" if group["success"] else "FAIL") + " " + json.dumps(group["counts"]), flush=True)
                write_json(output / "validation.json", result)
        result["source_integrity"] = dict(unchanged=before == snapshot(), checked_files=len(before))
        if not result["source_integrity"]["unchanged"]:
            result["release_blockers"].append("Sources changed during validation")
        for name, group in result["groups"].items():
            if not group["success"]:
                result["release_blockers"].append(name + ": " + str(group.get("issues", group.get("runner_errors"))))
        # Auxiliary fixture conversion is NOT a perception test pass or a drop pass.
        evidence_path = output / "perception/perception_test_report.json"
        result["auxiliary_drop_diagnostics"] = []
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text(encoding="utf8"))
            result["auxiliary_drop_diagnostics"] = [e for e in evidence if "cad_drop_success" in e]
            for item in result["auxiliary_drop_diagnostics"]:
                if item["cad_drop_success"] is not True:
                    result["release_blockers"].append("Unresolved auxiliary CAD drop failure: perception fixture " + item["fixture"])
            if not result["auxiliary_drop_diagnostics"]:
                result["release_blockers"].append("Perception fixture drop diagnostics are missing from its evidence report")
        else:
            result["release_blockers"].append("Perception evidence report is missing")
        result["success"] = not result["release_blockers"]
    result["framework_counts"] = {s: sum(g["counts"].get(s, 0) for n, g in result["groups"].items() if n in SUITES) for s in STATUSES}
    result["acceptance_counts"] = {s: sum(g["counts"].get(s, 0) for n, g in result["groups"].items() if n in ACCEPTANCE) for s in STATUSES}
    result["runner_error_count"] = sum(len(g.get("runner_errors", [])) for g in result["groups"].values())
    result["all_error_events"] = result["framework_counts"]["error"] + result["acceptance_counts"]["error"] + result["runner_error_count"]
    result["physics"] = {key: sum(g.get("physics", {}).get(key, 0) for g in result["groups"].values()) for key in ("test_methods", "native_steps", "fixture_steps")}
    result["test_gate_success"] = bool(result["groups"]) and len(result["groups"]) == len(SUITES) + len(ACCEPTANCE) and all(g["success"] for g in result["groups"].values())
    result["wall_seconds"] = time.perf_counter() - started
    write_json(output / "validation.json", result)
    print("Full validation " + ("PASS" if result["success"] else "FAIL") + ": " + str(output / "validation.json"), flush=True)
    return 0 if result["success"] else 1
