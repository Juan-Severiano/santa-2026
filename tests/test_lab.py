import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from santa2026.core import Puzzle, ValidationResult

_spec = importlib.util.spec_from_file_location("santa_lab", Path(__file__).resolve().parents[1] / "scripts" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


class LabTests(unittest.TestCase):
    def test_sample_spans_cost_distribution_without_duplicates(self):
        puzzles = [Puzzle(str(i), (1,), (1,), 1) for i in range(1000)]
        costs = {p.id: ValidationResult(int(p.id), 1) for p in puzzles}
        sample = lab.select_puzzles(puzzles, costs, 100)
        self.assertEqual(len({p.id for p in sample}), 100)
        self.assertLess(int(sample[0].id), 10)
        self.assertGreater(int(sample[-1].id), 990)

    def test_invalid_candidate_cannot_replace_baseline(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        original = [["/", 0, 0, 1]]
        module = SimpleNamespace(solve_shared=lambda *a, **kw: [])
        with patch.object(lab.importlib, "import_module", return_value=module):
            record = lab._evaluate((puzzle, original, ["shared"], [0], 1, "test"))
        self.assertEqual(record["program"], original)
        self.assertIn("error", record["attempts"][0])

    def test_mutating_optimizer_cannot_corrupt_baseline(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        original = [["/", 0, 0, 1]]

        def broken_optimizer(puzzle, program, **kwargs):
            program.clear()
            return program

        with patch.object(lab.importlib, "import_module", return_value=SimpleNamespace(optimize_program=broken_optimizer)):
            record = lab._evaluate((puzzle, original, ["optimize"], [0], 1, "test"))
        self.assertEqual(record["program"], original)
        self.assertEqual(len(original), 1)


if __name__ == "__main__":
    unittest.main()
