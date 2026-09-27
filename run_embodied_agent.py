"""Execute a structured JSON TaskPlan. No natural-language or LLM parsing."""
import argparse
import json
from pathlib import Path

from embodied_agent.models import TaskPlan
from embodied_agent.runner import run_agent


def main():
    parser = argparse.ArgumentParser(description="Run explicit robot skills from a structured TaskPlan")
    parser.add_argument("scene", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/embodied_agent"))
    parser.add_argument("--viewer", action="store_true", help="Show the live MuJoCo execution; close the window to stop")
    args = parser.parse_args()
    try:
        plan = TaskPlan.from_dict(json.loads(args.plan.read_text(encoding="utf8")))
        result = run_agent(args.scene, plan, args.output, viewer=args.viewer)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")
    print("Task:", result.status)
    print("Steps:", [(s["skill"], s["status"]) for s in result.steps])
    print("Failures:", result.failures)
    print("Results:", args.output.resolve())
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
