"""Reviewed collection counts. Changes require an explicit manifest update.

Unittest subTests are assertions within one case, not additional test cases.
Acceptance script cases are counted separately from framework test cases.
"""
SUITES = {
    "drawing2cad": ("pytest", "tests/drawing2cad", 112),
    "cad2mujoco": ("unittest", "tests/cad2mujoco", 13),
    "manipulation": ("unittest", "tests/manipulation", 13),
    "perception": ("unittest", "tests/perception", 14),
    "pick_place_task": ("unittest", "tests/pick_place_task", 18),
    "embodied_agent": ("unittest", "tests/embodied_agent", 23),
    "language_planner": ("unittest", "tests/language_planner", 24),
    "original_ball": ("unittest", "tests", 4),
    "validation_infrastructure": ("unittest", "tests/validation", 11),
    "e2e": ("unittest", "tests/e2e", 1),
}
ACCEPTANCE = {
    "b1": ("scripts/validate_drawing2cad_sample.py", "examples/washer/input.pdf", [], 6),
    "b2": ("scripts/validate_drawing2cad_profile.py", "tests/drawing2cad/fixtures/benchmark2.png", ["--units", "in"], 4),
    "b3": ("scripts/validate_drawing2cad_pattern.py", "tests/drawing2cad/fixtures/benchmark3.png", ["--units", "mm"], 5),
}
STATUSES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")


def counts(cases):
    return {name: sum(case.get("status") == name for case in cases) for name in STATUSES}


def gate(report, expected):
    """Fail closed on incomplete collection/execution or any non-pass outcome."""
    cases = report.get("cases", [])
    ids = [case.get("id") for case in cases]
    issues = []
    if report.get("collected") != expected:
        issues.append("collection_count_mismatch")
    if len(cases) != expected:
        issues.append("executed_count_mismatch")
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        issues.append("duplicate_or_missing_test_ids")
    if any(case.get("status") != "passed" for case in cases):
        issues.append("non_pass_outcome")
    if report.get("runner_errors"):
        issues.append("runner_errors")
    if report.get("returncode") != 0:
        issues.append("nonzero_exit")
    return {"success": not issues, "issues": issues, "counts": counts(cases), "expected": expected}
