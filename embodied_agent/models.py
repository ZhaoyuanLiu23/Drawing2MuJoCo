"""JSON contracts, using only the standard library."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math


SKILL_ORDER = ("observe", "locate", "pick", "place", "verify")


def validate_target(target):
    if not isinstance(target, dict) or set(target) != {"center_xy_m", "size_xy_m", "frame", "unit"}:
        raise ValueError("target requires center_xy_m, size_xy_m, frame and unit")
    if target["frame"] != "world" or target["unit"] != "m":
        raise ValueError("target must use world-frame metres")
    for key in ("center_xy_m", "size_xy_m"):
        values = target[key]
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError(key + " must contain two numbers")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError(key + " must contain finite numbers")
    if any(v <= 0 for v in target["size_xy_m"]):
        raise ValueError("target sizes must be positive")
    return dict(center_xy_m=list(target["center_xy_m"]), size_xy_m=list(target["size_xy_m"]), frame="world", unit="m")


def validate_goal(goal):
    if not isinstance(goal, dict) or set(goal) != {"type", "object", "target"}:
        raise ValueError("goal requires type, object and target")
    if goal["type"] != "pick_and_place" or not isinstance(goal["object"], str) or not goal["object"].strip():
        raise ValueError("Only an explicit pick_and_place goal with a nonempty object ID is supported")
    return dict(type="pick_and_place", object=goal["object"], target=validate_target(goal["target"]))


@dataclass
class SkillError:
    code: str
    message: str
    origin_stage: str = "unknown"
    details: dict = field(default_factory=dict)


@dataclass
class SkillResult:
    skill: str
    success: bool
    data: dict = field(default_factory=dict)
    error: SkillError = None

    def __post_init__(self):
        if self.skill not in SKILL_ORDER or type(self.success) is not bool or not isinstance(self.data, dict):
            raise ValueError("Invalid SkillResult contract")
        if self.success == (self.error is not None):
            raise ValueError("Successful skills have no error; failed skills must contain SkillError")
        if self.error is not None and not isinstance(self.error, SkillError):
            raise ValueError("Failed skills must contain a structured SkillError")

    def json(self):
        return deepcopy(asdict(self))

    @classmethod
    def failure(cls, skill, code, message, *, origin_stage="unknown", details=None, data=None):
        return cls(skill, False, data or {}, SkillError(code, str(message), origin_stage, details or {}))


@dataclass
class TaskPlan:
    goal: dict
    steps: list
    status: str = "pending"
    observations: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    @classmethod
    def build(cls, goal):
        goal = validate_goal(goal)
        steps = []
        for i, skill in enumerate(SKILL_ORDER):
            arguments = {} if skill == "observe" else {"object": goal["object"]}
            if skill in ("place", "verify"):
                arguments["target"] = deepcopy(goal["target"])
            steps.append(dict(id=f"step_{i + 1}", skill=skill, arguments=arguments, status="pending", result=None))
        return cls(goal, steps)

    def validate_pending(self):
        expected = self.build(self.goal).json()
        if self.json() != expected:
            raise ValueError("TaskPlan must be a fresh canonical observe/locate/pick/place/verify plan; no resume or arbitrary actions")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"goal", "steps", "status", "observations", "failures"}:
            raise ValueError("TaskPlan requires goal, steps, status, observations and failures")
        plan = cls(**deepcopy(value))
        plan.validate_pending()
        return plan

    def json(self):
        return deepcopy(asdict(self))
