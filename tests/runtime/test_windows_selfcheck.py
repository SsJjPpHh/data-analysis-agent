"""Report semantics can be verified without Windows, R or application imports."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/windows_selfcheck.py'


class ReportTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('windows_selfcheck', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def parse(self, xml):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.xml'
            path.write_text(xml, encoding='utf-8')
            return self.module.read_junit(path)

    def test_skip_is_not_acceptance(self):
        rows = self.parse('<testsuite><testcase name="native"><skipped message="no R"/></testcase></testsuite>')
        self.assertEqual(rows[0]['status'], 'SKIP')
        self.assertEqual(self.module.exit_code(rows), 2)

    def test_failure_and_collection_error_are_distinct(self):
        rows = self.parse('<testsuite><testcase name="boundary"><failure>assert denied</failure></testcase>'
                          '<testcase name="collection"><error>ImportError</error></testcase></testsuite>')
        self.assertEqual([r['category'] for r in rows], ['suspected_defect', 'unclassified'])
        self.assertEqual(self.module.exit_code(rows), 1)

    def test_empty_report_cannot_pass(self):
        self.assertEqual(self.module.exit_code([]), 2)

    def test_all_required_checks_must_pass(self):
        rows = self.parse('<testsuite><testcase name="native"/></testsuite>')
        self.assertEqual(self.module.exit_code(rows), 0)

    def test_incomplete_successful_pytest_run_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            def fake_run(command, **kwargs):
                xml = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
                Path(xml).write_text('<testsuite><testcase name="only_one"/></testsuite>')
                return subprocess.CompletedProcess(command, 0)
            with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                rows = self.module.run_tests("native", "target", output, {}, 4)
            self.assertEqual(self.module.exit_code(rows), 1)
            self.assertIn("expected 4", rows[-1]["detail"])

    @unittest.skipIf(sys.platform == "win32", "non-Windows safety gate")
    def test_non_windows_cli_reports_incomplete(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report"
            completed = subprocess.run([sys.executable, str(SCRIPT), "--output", str(output)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2, completed.stderr)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["acceptance"], "INCOMPLETE")
            self.assertEqual(report["checks"][0]["status"], "SKIP")


if __name__ == '__main__':
    unittest.main()
