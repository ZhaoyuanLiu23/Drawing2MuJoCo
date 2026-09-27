"""Gate tests do not count injected failures as real pipeline validation."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from validation_tools.manifest import gate
from validation_tools.runner import main, preflight
from validation_tools.worker import run_unittest


class ValidationGateTests(unittest.TestCase):
    def report(self, status="passed"):
        return dict(collected=1, cases=[dict(id="case", status=status)], runner_errors=[], returncode=0)

    def test_exact_complete_pass(self):
        self.assertTrue(gate(self.report(), 1)["success"])

    def test_missing_extra_or_unexecuted_cases_fail(self):
        for expected in (0, 2):
            with self.subTest(expected=expected):
                self.assertFalse(gate(self.report(), expected)["success"])
        report = self.report()
        report["cases"] = []
        self.assertIn("executed_count_mismatch", gate(report, 1)["issues"])

    def test_skip_never_means_pass(self):
        result = gate(self.report("skipped"), 1)
        self.assertFalse(result["success"])
        self.assertEqual(result["counts"]["skipped"], 1)
        self.assertEqual(result["counts"]["passed"], 0)

    def test_xfail_xpass_unknown_all_fail(self):
        for status in ("xfailed", "xpassed", "unknown"):
            with self.subTest(status=status):
                self.assertFalse(gate(self.report(status), 1)["success"])

    def test_failure_and_error_counted_separately(self):
        for status in ("failed", "error"):
            with self.subTest(status=status):
                result = gate(self.report(status), 1)
                self.assertFalse(result["success"])
                self.assertEqual(result["counts"][status], 1)

    def test_duplicate_id_rejected(self):
        report = self.report()
        report["cases"] *= 2
        report["collected"] = 2
        self.assertIn("duplicate_or_missing_test_ids", gate(report, 2)["issues"])

    def test_setup_error_or_crash_cannot_hide_behind_pass(self):
        report = self.report()
        report["runner_errors"] = [dict(status="error", detail="setUpClass failed")]
        self.assertFalse(gate(report, 1)["success"])
        report = self.report()
        report["returncode"] = 1
        self.assertFalse(gate(report, 1)["success"])

    def test_missing_panda_is_failed_preflight(self):
        with patch("validation_tools.runner.probe", return_value={"success": True}):
            result = preflight("python", "cad-python", None)
        self.assertFalse(result["success"])
        self.assertFalse(result["checks"]["panda_scene"]["success"])

    def test_missing_environment_writes_failure_json_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("validation_tools.runner.preflight", return_value={"success": False}), patch("sys.stdout", new=io.StringIO()):
                code = main(["--output", directory])
            result = json.loads((Path(directory) / "validation.json").read_text(encoding="utf8"))
        self.assertEqual(code, 1)
        self.assertFalse(result["success"])
        self.assertFalse(result["test_gate_success"])
        self.assertTrue(result["blocked_groups"])
        self.assertEqual(sum(result["framework_counts"].values()), 0)

    def test_worker_preserves_real_unittest_negative_outcomes(self):
        # Intentional contract fixtures; not counted as successful robot tests.
        source = '''import unittest
class Outcomes(unittest.TestCase):
 def test_pass(self): pass
 def test_fail(self): self.fail('injected')
 def test_error(self): raise RuntimeError('injected')
 @unittest.skip('missing environment')
 def test_skip(self): pass
 @unittest.expectedFailure
 def test_xfail(self): self.fail('expected')
 @unittest.expectedFailure
 def test_xpass(self): pass
 def test_subtest_failure(self):
  with self.subTest(case=1): self.fail('subtest')
'''
        import mujoco
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "test_injected_gate_outcomes.py").write_text(source)
            report = dict(cases=[], runner_errors=[])
            with patch("sys.stderr", new=io.StringIO()), patch.object(mujoco, "mj_step", mujoco.mj_step):
                code = run_unittest(directory, report)
        report["returncode"] = code
        result = gate(report, 7)
        self.assertFalse(result["success"])
        self.assertEqual(result["counts"], dict(passed=1, failed=2, error=1, skipped=1, xfailed=1, xpassed=1))
        self.assertEqual(report["physics"]["native_steps"], 0)

    def test_pytest_worker_negative_outcomes_and_teardown_error(self):
        root = Path(__file__).resolve().parents[2]
        python = os.environ.get("CAD_TEST_PYTHON", str(root / ".venv-drawing2cad/Scripts/python.exe"))
        source = '''import pytest
def test_pass(): pass
def test_fail(): assert False
@pytest.fixture
def bad_setup(): raise RuntimeError('setup')
def test_setup(bad_setup): pass
@pytest.mark.skip(reason='unavailable')
def test_skip(): pass
@pytest.mark.xfail
def test_xfail(): assert False
@pytest.mark.xfail
def test_xpass(): pass
@pytest.mark.xfail(strict=True)
def test_strict_xpass(): pass
@pytest.fixture
def bad_teardown():
 yield
 raise RuntimeError('teardown')
def test_teardown(bad_teardown): pass
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "test_injected_pytest_outcomes.py").write_text(source)
            code = ("import json,sys; from pathlib import Path; from validation_tools.worker import run_pytest; "
                    "r=dict(cases=[],runner_errors=[]); r['returncode']=run_pytest(sys.argv[1],r); "
                    "Path(sys.argv[2]).write_text(json.dumps(r))")
            process = subprocess.run([python, "-c", code, directory, str(path / "result.json")], cwd=root,
                                     capture_output=True, text=True, encoding="utf8", errors="replace", timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            report = json.loads((path / "result.json").read_text())
        result = gate(report, 8)
        self.assertFalse(result["success"])
        self.assertEqual(result["counts"], dict(passed=1, failed=1, error=2, skipped=1, xfailed=1, xpassed=2),
                         json.dumps(report) + "\n" + process.stdout + process.stderr)
