"""Structured planning failures; no robot or transport state."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field


@dataclass
class PlanningFailure:
    code: str
    stage: str
    message: str
    details: dict = field(default_factory=dict)

    def json(self):
        return deepcopy(asdict(self))


class PlanningRejected(Exception):
    def __init__(self, code, stage, message, **details):
        self.failure = PlanningFailure(code, stage, message, details)
        super().__init__(message)


@dataclass
class PlanningResult:
    success: bool
    task_plan: dict = None
    failure: PlanningFailure = None
    metadata: dict = field(default_factory=dict)

    def json(self):
        return deepcopy(asdict(self))
