import csv
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from santa2026.cli import main
from santa2026.core import read_puzzles, read_submission, score_submission, write_submission


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.puzzles = self.root / "puzzles.csv"
        with self.puzzles.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["id", "starting_values", "targets", "value_limit", "registers"])
            writer.writerow(["example_0001", "[3]", "[1,2,5,8]", 9, 48])

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(list(map(str, args)))
        return code, output.getvalue(), error.getvalue()

    def test_solve_serialized_output_validates_and_report_matches(self):
        target, report = self.root / "submission.csv", self.root / "report.json"
        code, output, error = self.invoke("solve", self.puzzles, target, "--report", report)
        self.assertEqual((code, error), (0, ""))
        programs = read_submission(target)
        result = score_submission(read_puzzles(self.puzzles), programs)
        self.assertEqual(json.loads(output)["score"], result["example_0001"].instructions)
        self.assertEqual(json.loads(report.read_text())["puzzles"], 1)

    def test_validation_failure_is_nonzero_and_no_success_report(self):
        target, report = self.root / "bad.csv", self.root / "report.json"
        target.write_text('id,program\nexample_0001,"[]"\n')
        code, output, error = self.invoke("validate", self.puzzles, target, "--report", report)
        self.assertEqual(code, 2)
        self.assertIn("misses", error)
        self.assertEqual(output, "")
        self.assertFalse(report.exists())

    def test_inspect_lower_bound(self):
        code, output, error = self.invoke("inspect", self.puzzles)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["target_count_lower_bound"], 4)

    def test_solver_value_error_keeps_valid_fallback_and_reports_failure(self):
        fallback = self.root / "fallback.csv"
        target, report = self.root / "submission.csv", self.root / "report.json"
        program = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3], ["+", 0, 3, 4]]
        write_submission(fallback, {"example_0001": program})
        with patch("santa2026.cli.solve", side_effect=ValueError("search exhausted")) as solver:
            code, output, error = self.invoke(
                "solve", self.puzzles, target, "--fallback", fallback, "--report", report
            )
        self.assertEqual((code, error), (0, ""))
        solver.assert_called_once_with(read_puzzles(self.puzzles)[0], strategy="portfolio")
        self.assertEqual(read_submission(target), {"example_0001": program})
        saved = json.loads(report.read_text())
        self.assertEqual(saved["solver_failures"], {"example_0001": "search exhausted"})
        self.assertEqual(saved["score"], 4)
        self.assertEqual(saved["fallback_score"], 4)
        self.assertEqual(saved["instructions_saved"], 0)
        self.assertEqual(saved["submission_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(json.loads(output)["score"], 4)

    def test_shorter_fallback_prevents_score_regression(self):
        fallback = self.root / "fallback.csv"
        target = self.root / "submission.csv"
        program = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3], ["+", 0, 3, 4]]
        write_submission(fallback, {"example_0001": program})
        longer = program + [["/", 4, 4, 4]]
        with patch("santa2026.cli.solve", return_value=longer):
            code, output, error = self.invoke("solve", self.puzzles, target, "--fallback", fallback)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(read_submission(target), {"example_0001": program})
        self.assertEqual(json.loads(output)["score"], 4)
        self.assertEqual(json.loads(output)["instructions_saved"], 0)
        self.assertEqual(json.loads(output)["puzzles_improved"], 0)


if __name__ == "__main__":
    unittest.main()
