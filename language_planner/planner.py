"""Natural language to validated plans. Deliberately contains no execution API."""
from .catalog import CapabilityCatalog
from .errors import PlanningRejected, PlanningResult
from .prompt import PROMPT_VERSION, make_request
from .providers import ProviderUnavailable
from .validation import PlanValidator, strict_json


class Planner:
    def __init__(self, provider, catalog_source):
        self.provider = provider
        self.catalog_source = catalog_source

    def _catalog(self):
        try:
            value = self.catalog_source()
            return CapabilityCatalog(value.json() if isinstance(value, CapabilityCatalog) else value)
        except PlanningRejected:
            raise
        except Exception as exc:
            raise PlanningRejected("CATALOG_UNAVAILABLE", "catalog", "Cannot obtain current allowed capabilities") from exc

    def plan(self, instruction):
        metadata = dict(provider=self.provider.name, prompt_version=PROMPT_VERSION, planning_only=True)
        try:
            if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 8192:
                raise PlanningRejected("INVALID_INSTRUCTION", "input", "Instruction must be nonempty text of at most 8192 characters")
            catalog = self._catalog()
            catalog.require_sequence()
            validator = PlanValidator()
            metadata.update(catalog_sha256=catalog.sha256, schema_sha256=validator.sha256)
            try:
                raw = self.provider.complete(make_request(instruction, catalog, validator.schema))
            except ProviderUnavailable as exc:
                raise PlanningRejected("LLM_UNAVAILABLE", "provider", "Provider is unavailable; no fallback was used") from exc
            except Exception as exc:
                raise PlanningRejected("PROVIDER_ERROR", "provider", "Provider failed; no fallback was used", exception_type=type(exc).__name__) from exc
            value = strict_json(raw)
            if isinstance(value, dict) and "planning_failure" in value:
                failure = value["planning_failure"]
                allowed = {"UNKNOWN_OBJECT", "UNKNOWN_TARGET", "AMBIGUOUS_INSTRUCTION", "UNSUPPORTED_INSTRUCTION"}
                if set(value) != {"planning_failure"} or not isinstance(failure, dict) or set(failure) != {"code", "message"} or not isinstance(failure["code"], str) or failure["code"] not in allowed or not isinstance(failure["message"], str) or not failure["message"].strip():
                    raise PlanningRejected("MALFORMED_MODEL_OUTPUT", "parsing", "Invalid structured provider failure")
                raise PlanningRejected(failure["code"], "interpretation", failure["message"][:1000])
            plan, target_id = validator.validate(value, catalog)
            metadata.update(target_id=target_id, schema_validated=True, contract_validated=True, allowlist_validated=True)
            return PlanningResult(True, task_plan=plan.json(), metadata=metadata)
        except PlanningRejected as exc:
            return PlanningResult(False, failure=exc.failure, metadata=metadata)
        except Exception as exc:
            return PlanningResult(False, failure=PlanningRejected("PLANNER_ERROR", "planning", "Planner could not validate the result", exception_type=type(exc).__name__).failure, metadata=metadata)

    def validated_task_plan(self, result):
        """Revalidate before caller hands a plan to embodied_agent; never execute it.

        Raises PlanningRejected with .failure if the result/capabilities changed.
        """
        if not isinstance(result, PlanningResult) or result.success is not True or result.failure is not None:
            raise PlanningRejected("PLAN_NOT_READY", "handoff", "Only a successful planning result can be handed off")
        catalog = self._catalog()
        validator = PlanValidator()
        if result.metadata.get("catalog_sha256") != catalog.sha256 or result.metadata.get("schema_sha256") != validator.sha256:
            raise PlanningRejected("STALE_CAPABILITIES", "handoff", "Catalog or TaskPlan schema changed since planning")
        plan, _ = validator.validate(result.task_plan, catalog)
        return plan
