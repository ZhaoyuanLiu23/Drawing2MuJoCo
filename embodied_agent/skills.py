"""Small public robot-skill surface; no actuator or simulation-state access."""
from copy import deepcopy

from .models import SKILL_ORDER, SkillResult, validate_target


class RobotSkills:
    def __init__(self, session):
        self._session = session
        self._goal = deepcopy(session.goal)
        self._next = 0
        self._terminal = False

    def _call(self, skill, object=None, target=None):
        if self._terminal or self._next >= len(SKILL_ORDER) or SKILL_ORDER[self._next] != skill:
            return self._fail(skill, "INVALID_SKILL_STATE", "Skills must run once in observe/locate/pick/place/verify order")
        if skill != "observe" and object != self._goal["object"]:
            return self._fail(skill, "OBJECT_MISMATCH", "Object differs from the session's explicit goal")
        if skill in ("place", "verify"):
            try:
                target = validate_target(target)
            except ValueError as exc:
                return self._fail(skill, "INVALID_TARGET", str(exc))
            if target != self._goal["target"]:
                return self._fail(skill, "TARGET_MISMATCH", "Target must match the plan used for full-path preflight")
        try:
            result = self._session.advance(skill)
        except Exception as exc:
            return self._fail(skill, "SKILL_BACKEND_EXCEPTION", str(exc))
        if not isinstance(result, SkillResult) or result.skill != skill:
            return self._fail(skill, "INVALID_SKILL_RESULT", "Pipeline returned an invalid skill result")
        if result.success:
            self._next += 1
        else:
            self._terminal = True
            self._session.close()
        return result

    def _fail(self, skill, code, message):
        self._terminal = True
        self._session.close()
        return SkillResult.failure(skill, code, message, origin_stage="skill_contract")

    def observe(self):
        return self._call("observe")

    def locate(self, object):
        return self._call("locate", object=object)

    def pick(self, object):
        return self._call("pick", object=object)

    def place(self, object, target):
        return self._call("place", object=object, target=target)

    def verify(self, object, target):
        return self._call("verify", object=object, target=target)

    def close(self):
        self._terminal = True
        self._session.close()
