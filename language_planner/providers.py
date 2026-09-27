"""Independent provider adapters: offline grammar, raw mock, or injected LLM call."""
from copy import deepcopy
import json
import re
import unicodedata
from typing import Protocol

from embodied_agent.models import TaskPlan
from .catalog import CapabilityCatalog
from .errors import PlanningRejected
from .prompt import PlanningRequest


class ProviderUnavailable(RuntimeError):
    pass


class PlannerProvider(Protocol):
    name: str

    def complete(self, request: PlanningRequest) -> str:
        """Return raw TaskPlan JSON or the specified planning_failure envelope."""
        ...


class CallableLLMProvider:
    """Inject completion(messages=[...]) -> raw text; no SDK or credential dependency.

    The host's completion callable owns network timeouts and response extraction.
    The planner invokes it exactly once, never retries or falls back to mock output.
    """
    name = "llm-callable"

    def __init__(self, completion):
        self.completion = completion

    def complete(self, request):
        if not callable(self.completion):
            raise ProviderUnavailable("No LLM completion callable configured")
        try:
            return self.completion(messages=deepcopy(list(request.messages)))
        except Exception as exc:
            # SDK errors may contain credentials, endpoints or request bodies.
            raise ProviderUnavailable("LLM completion unavailable") from exc


class MockProvider:
    """Return caller-supplied text unchanged so tests exercise all trust boundaries."""
    name = "mock"

    def __init__(self, output=None, *, unavailable=False):
        self.output, self.unavailable = output, unavailable

    def complete(self, request):
        if self.unavailable:
            raise ProviderUnavailable("Mock provider unavailable")
        return self.output


class DeterministicProvider:
    """Small anchored bilingual grammar. No fuzzy matching, defaults or LLM fallback."""
    name = "deterministic"
    patterns = (
        r"(?:请)?(?:把|将)(?P<object>.+?)(?:放置到|移动到|搬运到|搬到|移到|放到|放在)(?P<target>.+)",
        r"(?:please\s+)?(?:put|place|move|transfer)\s+(?:the\s+)?(?P<object>.+?)\s+(?:into|onto|in|to|on)\s+(?:the\s+)?(?P<target>.+)",
    )

    def complete(self, request):
        text = unicodedata.normalize("NFKC", request.instruction).strip().rstrip("。.!！").strip()
        match = next((m for p in self.patterns if (m := re.fullmatch(p, text, re.IGNORECASE))), None)
        if not match:
            return self._failure("UNSUPPORTED_INSTRUCTION", "Use an explicit single-object placement sentence with registered names")
        catalog = CapabilityCatalog(request.catalog)
        try:
            obj = catalog.resolve_name("objects", match.group("object"))
            target = catalog.resolve_name("targets", match.group("target"))
        except PlanningRejected as exc:
            # Same error envelope as an LLM; no privileged validation bypass.
            code = "AMBIGUOUS_INSTRUCTION" if exc.failure.code == "AMBIGUOUS_REFERENCE" else exc.failure.code
            return self._failure(code, exc.failure.message)
        plan = TaskPlan.build(dict(type="pick_and_place", object=obj["id"], target=target["region"]))
        return json.dumps(plan.json(), ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _failure(code, message):
        return json.dumps(dict(planning_failure=dict(code=code, message=message)), ensure_ascii=False)
