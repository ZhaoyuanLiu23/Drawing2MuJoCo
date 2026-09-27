"""Isolated test process; JSON includes collection, every verdict and real steps."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback
import unittest

from .manifest import SUITES, counts


def run_unittest(path, report):
    steps = {"total": 0, "fixtures": 0}
    active = [None]
    if importlib.util.find_spec("mujoco"):
        import mujoco
        original_step = mujoco.mj_step

        def step(*args, **kwargs):
            n = int(kwargs.get("nstep", args[2] if len(args) > 2 else 1))
            result = original_step(*args, **kwargs)
            steps["total"] += n
            if active[0] is None:
                steps["fixtures"] += n
            else:
                active[0]["physics_steps"] += n
            return result
        mujoco.mj_step = step

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            active[0] = dict(id=test.id(), status="error", physics_steps=0, started=time.perf_counter())

        def outcome(self, test, status, detail=None):
            row = active[0]
            if row is None:  # e.g. setUpClass, failed discovery/import
                report["runner_errors"].append(dict(id=test.id(), status=status, detail=detail))
            else:
                row["status"] = status
                if detail:
                    row.setdefault("details", []).append(detail)

        def addSuccess(self, test):
            super().addSuccess(test)
            self.outcome(test, "passed")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.outcome(test, "failed", self._exc_info_to_string(err, test))

        def addError(self, test, err):
            super().addError(test, err)
            self.outcome(test, "error", self._exc_info_to_string(err, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.outcome(test, "skipped", reason)

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self.outcome(test, "xfailed", self._exc_info_to_string(err, test))

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.outcome(test, "xpassed")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err:
                self.outcome(test, "failed" if issubclass(err[0], test.failureException) else "error",
                             str(subtest) + "\n" + self._exc_info_to_string(err, test))

        def stopTest(self, test):
            row = active[0]
            row["seconds"] = time.perf_counter() - row.pop("started")
            report["cases"].append(row)
            active[0] = None
            super().stopTest(test)

    suite = unittest.TestLoader().discover(path, pattern="test_*.py")
    report["collected"] = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    report["physics"] = dict(test_methods=sum(c["physics_steps"] > 0 for c in report["cases"]),
                             native_steps=steps["total"], fixture_steps=steps["fixtures"])
    return 0 if result.wasSuccessful() else 1


def run_pytest(path, report):
    import pytest
    rows = {}

    class Plugin:
        def pytest_collection_finish(self, session):
            report["collected"] = len(session.items)

        def pytest_collectreport(self, report):
            if report.failed:
                outer["runner_errors"].append(dict(id=report.nodeid, status="error", detail=str(report.longrepr)))

        def pytest_runtest_logreport(self, report):
            rep = report
            row = rows.setdefault(rep.nodeid, dict(id=rep.nodeid, status="error", seconds=0., physics_steps=0))
            row["seconds"] += rep.duration
            if hasattr(rep, "wasxfail"):
                row["status"] = "xfailed" if rep.skipped else "xpassed"
            elif rep.failed:
                row["status"] = ("xpassed" if str(rep.longrepr).startswith("[XPASS(strict)]")
                                 else "failed" if rep.when == "call" else "error")
                row.setdefault("details", []).append(str(rep.longrepr))
            elif rep.skipped:
                row["status"] = "skipped"
                row.setdefault("details", []).append(str(rep.longrepr))
            elif rep.when == "call":
                row["status"] = "passed"
    outer = report
    target = Path(path).resolve()
    root = target if target.is_dir() else target.parent
    # Bound collection to this suite, including temporary negative-test fixtures.
    # Otherwise pytest can choose a common ancestor outside the repository.
    code = pytest.main([str(target), "--rootdir=" + str(root), "-q", "-p", "no:cacheprovider"], plugins=[Plugin()])
    report["cases"] = list(rows.values())
    return int(code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = dict(suite=args.suite, collected=0, cases=[], runner_errors=[], returncode=1)
    start = time.perf_counter()
    try:
        kind, path, _ = SUITES[args.suite]
        report["returncode"] = run_pytest(path, report) if kind == "pytest" else run_unittest(path, report)
    except BaseException:
        report["runner_errors"].append(dict(status="error", detail=traceback.format_exc()))
    finally:
        report["seconds"] = time.perf_counter() - start
        report["counts"] = counts(report["cases"])
        dest = Path(args.report)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf8")
    return report["returncode"]


if __name__ == "__main__":
    sys.exit(main())
