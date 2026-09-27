"""Offline planning CLI. Produces evidence and a validated TaskPlan; never runs it."""
import argparse
import json
from pathlib import Path

from language_planner import DeterministicProvider, MockProvider, Planner, PlanningRejected, PlanningResult


def main():
    parser = argparse.ArgumentParser(description="Natural-language instruction -> validated TaskPlan (planning only)")
    parser.add_argument("instruction")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/language_planner"))
    parser.add_argument("--provider", choices=("deterministic", "mock"), default="deterministic")
    parser.add_argument("--model-output", type=Path, help="Raw model response file for mock testing only")
    args = parser.parse_args()
    if (args.provider == "mock") != (args.model_output is not None):
        parser.error("--model-output is required only for --provider mock")
    try:
        output = args.output.resolve()
        for source in (args.catalog, args.model_output):
            if source is not None and (output == source.resolve() or output in source.resolve().parents):
                raise ValueError("Output must not contain an input file")
        marker = output / ".language-planner-output"
        if output.exists() and any(output.iterdir()) and not marker.is_file():
            raise ValueError("Choose an empty or existing language-planner output directory")
        output.mkdir(parents=True, exist_ok=True)
        marker.write_text("Planning-only generated evidence\n", encoding="utf8")
        # Remove only our own old plan, so a failed rerun cannot look executable.
        plan_file = output / "task_plan.json"
        if plan_file.is_file():
            plan_file.unlink()
        provider = DeterministicProvider() if args.provider == "deterministic" else MockProvider(args.model_output.read_text(encoding="utf8"))
        planner = Planner(provider, lambda: json.loads(args.catalog.read_text(encoding="utf-8-sig")))
        result = planner.plan(args.instruction)
        if result.success:
            try:
                ready = planner.validated_task_plan(result)
            except PlanningRejected as exc:
                result = PlanningResult(False, failure=exc.failure, metadata=result.metadata)
            else:
                plan_file.write_text(json.dumps(ready.json(), ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf8")
        (output / "planning_result.json").write_text(json.dumps(result.json(), ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf8")
        (output / "instruction.txt").write_text(args.instruction+"\n", encoding="utf8")
        print(json.dumps(dict(success=result.success, failure=result.failure.json() if result.failure else None,
                             task_plan=str(plan_file) if result.success else None, executed=False), ensure_ascii=False))
        return 0 if result.success else 1
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(success=False, failure=dict(code="CLI_INPUT_OUTPUT_ERROR", stage="cli", message=str(exc), details={}), executed=False), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
