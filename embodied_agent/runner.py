"""Persist the explicit plan and run the deterministic Agent once."""
from pathlib import Path
import json

from .agent import Agent
from .backend import PipelineSession
from .skills import RobotSkills


def run_agent(scene, plan, output, *, task_factory=None, viewer=False):
    plan.validate_pending()
    output, scene = Path(output).resolve(), Path(scene).resolve()
    if scene == output or output in scene.parents:
        raise ValueError("Agent output must not contain the input scene")
    marker = output / ".embodied-agent-output"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError("Choose an empty or existing embodied-agent output")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text("Deterministic agent task evidence\n", encoding="utf8")
    (output / "input_plan.json").write_text(json.dumps(plan.json(), ensure_ascii=False, indent=2), encoding="utf8")
    transitions = []

    def save(value):
        transitions.append(dict(status=value["status"], steps=[dict(id=s["id"], status=s["status"]) for s in value["steps"]]))
        temporary = output / "task_plan.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
        temporary.replace(output / "task_plan.json")
        (output / "transitions.json").write_text(json.dumps(transitions, indent=2), encoding="utf8")

    session = PipelineSession(scene, plan.goal, output / "skills", task_factory=task_factory, viewer=viewer)
    return Agent(RobotSkills(session)).execute(plan, on_update=save)
