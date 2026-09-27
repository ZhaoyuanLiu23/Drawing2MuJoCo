"""Untrusted model text -> exact existing schema -> canonical pending TaskPlan."""
from pathlib import Path
import hashlib
import json
import math

from jsonschema import Draft202012Validator
from embodied_agent.models import SKILL_ORDER, TaskPlan
from .errors import PlanningRejected


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas/task_plan.schema.json"
MAX_OUTPUT_BYTES = 131072


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("Nonfinite JSON number")

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite JSON number")
        if isinstance(value, dict):
            for v in value.values():
                finite(v)
        if isinstance(value, list):
            for v in value:
                finite(v)

    try:
        if not isinstance(text, str) or len(text.encode("utf8")) > MAX_OUTPUT_BYTES:
            raise ValueError("Expected bounded JSON text")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        finite(value)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise PlanningRejected("MALFORMED_MODEL_OUTPUT", "parsing", "Provider must return one strict JSON value without Markdown, duplicate keys or nonfinite numbers") from exc


class PlanValidator:
    def __init__(self):
        raw = SCHEMA_PATH.read_bytes()
        self.schema = json.loads(raw)
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)
        self.sha256 = hashlib.sha256(raw).hexdigest()

    def validate(self, value, catalog):
        # Give unknown operations an explicit error before the general schema check.
        if isinstance(value, dict) and isinstance(value.get("steps"), list):
            for step in value["steps"]:
                if isinstance(step, dict) and "skill" in step and step["skill"] not in SKILL_ORDER:
                    raise PlanningRejected("ILLEGAL_SKILL", "allowlist", "Only the five existing robot skills are allowed")
        errors = sorted(self.validator.iter_errors(value), key=lambda e: str(list(e.absolute_path)))
        if errors:
            # Do not echo raw model strings, controller values or credentials into diagnostics.
            raise PlanningRejected("SCHEMA_VALIDATION_FAILED", "schema", "Model output does not satisfy the existing task_plan.schema.json",
                                   violations=[dict(path=list(e.absolute_path), rule=e.validator) for e in errors[:12]])
        catalog.require_sequence()
        target_id = catalog.validate_goal(value["goal"])
        try:
            plan = TaskPlan.from_dict(value)
        except (ValueError, TypeError, KeyError) as exc:
            raise PlanningRejected("PLAN_CONTRACT_FAILED", "contract", "Plan must be fresh and canonical; every step must agree with its goal") from exc
        return plan, target_id
