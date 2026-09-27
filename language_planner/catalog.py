"""Explicit capability registry supplied by the application, never by the LLM."""
from copy import deepcopy
import hashlib
import json
import re
import unicodedata

from embodied_agent.models import SKILL_ORDER, validate_target
from .errors import PlanningRejected


def normalize_name(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf8")).hexdigest()


class CapabilityCatalog:
    def __init__(self, value):
        try:
            self._value = self._validate(deepcopy(value))
        except (ValueError, TypeError, KeyError) as exc:
            raise PlanningRejected("INVALID_CATALOG", "catalog", str(exc)) from exc

    @staticmethod
    def _validate(value):
        if not isinstance(value, dict) or set(value) != {"objects", "targets", "skills"}:
            raise ValueError("Catalog requires objects, targets and skills")
        for group in ("objects", "targets"):
            entries = value[group]
            if not isinstance(entries, list) or not entries:
                raise ValueError(group + " must be a nonempty list")
            ids = set()
            for item in entries:
                expected = {"id", "aliases"} | ({"region"} if group == "targets" else set())
                if not isinstance(item, dict) or set(item) != expected:
                    raise ValueError(group + " entry has invalid fields")
                name = item["id"]
                if not isinstance(name, str) or not name.strip() or name != name.strip() or name in ids:
                    raise ValueError(group + " IDs must be nonempty, unique strings without boundary spaces")
                ids.add(name)
                aliases = item["aliases"]
                if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
                    raise ValueError("aliases must be a list of nonempty strings")
                if group == "targets":
                    item["region"] = validate_target(item["region"])
            if group == "targets":
                regions = [item["region"] for item in entries]
                if any(region in regions[:i] for i, region in enumerate(regions)):
                    raise ValueError("Targets must have distinct regions: existing TaskPlan has no target ID field")
        skills = value["skills"]
        if not isinstance(skills, list) or any(not isinstance(s, str) or s not in SKILL_ORDER for s in skills) or len(set(skills)) != len(skills):
            raise ValueError("Catalog skills must be a unique subset of the five existing skills")
        return value

    def json(self):
        return deepcopy(self._value)

    @property
    def sha256(self):
        return fingerprint(self._value)

    def require_sequence(self):
        missing = [s for s in SKILL_ORDER if s not in self._value["skills"]]
        if missing:
            raise PlanningRejected("MISSING_REQUIRED_SKILL", "catalog", "The existing TaskPlan requires all five skills", missing=missing)

    def resolve_name(self, group, reference):
        key = normalize_name(reference)
        matches = [item for item in self._value[group] if key in {normalize_name(s) for s in [item["id"]] + item["aliases"]}]
        if not matches:
            raise PlanningRejected("UNKNOWN_OBJECT" if group == "objects" else "UNKNOWN_TARGET", "grounding", "Reference is not registered", reference=reference)
        if len(matches) > 1:
            raise PlanningRejected("AMBIGUOUS_REFERENCE", "grounding", "Reference matches more than one registered entity", reference=reference, matches=[m["id"] for m in matches])
        return deepcopy(matches[0])

    def validate_goal(self, goal):
        if goal["object"] not in {item["id"] for item in self._value["objects"]}:
            raise PlanningRejected("UNKNOWN_OBJECT", "allowlist", "Plan object ID is not registered")
        matches = [t for t in self._value["targets"] if goal["target"] == t["region"]]
        if len(matches) != 1:
            raise PlanningRejected("UNKNOWN_TARGET", "allowlist", "Plan target must exactly equal one registered region; coordinate invention is prohibited")
        return matches[0]["id"]
