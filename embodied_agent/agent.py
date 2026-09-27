"""A deterministic dispatcher. Depends only on skill contracts, never a simulator."""
from .models import SkillResult


class Agent:
    def __init__(self, skills):
        self.skills = skills

    def execute(self, plan, *, on_update=None):
        plan.validate_pending()
        plan.status = "running"
        if on_update:
            on_update(plan.json())
        try:
            for index, step in enumerate(plan.steps):
                step["status"] = "running"
                if on_update:
                    on_update(plan.json())
                try:
                    result = getattr(self.skills, step["skill"])(**step["arguments"])
                    if not isinstance(result, SkillResult) or result.skill != step["skill"]:
                        raise ValueError("Skill returned an invalid result contract")
                except Exception as exc:
                    result = SkillResult.failure(step["skill"], "SKILL_EXCEPTION", str(exc),
                                                 details={"exception_type": type(exc).__name__})
                if result.success and step["skill"] == "verify":
                    checks = result.data.get("checks")
                    if result.data.get("verified") is not True or not isinstance(checks, dict) or not checks or not all(v is True for v in checks.values()):
                        result = SkillResult.failure("verify", "VERIFY_RESULT_INVALID", "Verify did not return positive visual checks", data=result.data)
                step["result"] = result.json()
                step["status"] = "succeeded" if result.success else "failed"
                if result.data and step["skill"] in ("observe", "locate", "verify"):
                    plan.observations.append(dict(step_id=step["id"], skill=step["skill"], data=result.json()["data"]))
                if not result.success:
                    plan.failures.append(dict(step_id=step["id"], skill=step["skill"], error=result.json()["error"]))
                    for later in plan.steps[index + 1:]:
                        later["status"] = "skipped"
                    plan.status = "failed"
                elif index == len(plan.steps) - 1:
                    plan.status = "succeeded"
                if on_update:
                    on_update(plan.json())
                if plan.status == "failed":
                    break
        finally:
            self.skills.close()
        return plan

