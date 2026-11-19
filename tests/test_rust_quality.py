import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle


SPEC = importlib.util.spec_from_file_location("rust_quality", Path(__file__).resolve().parents[1] / "scripts/rust_quality.py")
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


class RustQualityTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = Puzzle("toy", (3,), (5,), 10)
        self.original = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 1, 1, 3], ["+", 0, 2, 4]]

    def test_invalid_output_and_timeout_preserve_input(self):
        invalid = subprocess.CompletedProcess([], 0, "puzzle []\n", "")
        with patch.object(quality.subprocess, "run", side_effect=[invalid, subprocess.TimeoutExpired("engine", 1)]):
            record = quality.search("repair", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], self.original)
        self.assertIsNot(record["program"], self.original)
        self.assertIn("error", record["attempts"][0])
        self.assertTrue(record["attempts"][1]["budget_exhausted"])

    def test_valid_smaller_program_survives_later_timeout(self):
        shorter = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3]]
        valid = subprocess.CompletedProcess([], 0, "puzzle " + json.dumps(shorter) + "\n", "")
        with patch.object(quality.subprocess, "run", side_effect=[valid, subprocess.TimeoutExpired("engine", 1)]):
            record = quality.search("tail", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], shorter)
        self.assertEqual(len(self.original), 4)

    def test_complete_valid_timeout_output_is_recovered(self):
        shorter = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3]]
        interrupted = subprocess.TimeoutExpired("engine", 1,
            output=("puzzle " + json.dumps(shorter) + "\n").encode())
        with patch.object(quality.subprocess, "run", side_effect=interrupted):
            record = quality.search("tail", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], shorter)
        self.assertTrue(record["attempts"][0]["recovered_complete_output"])

    def test_constructor_keeps_longer_diverse_candidate_without_regressing_best(self):
        longer = self.original + [["+", 1, 1, 5]]
        valid = subprocess.CompletedProcess([], 0, "puzzle " + json.dumps(longer) + "\n", "")
        with patch.object(quality.subprocess, "run", side_effect=[valid, subprocess.TimeoutExpired("engine", 1)]):
            record = quality.search("construct", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], self.original)
        self.assertEqual(record["generated_programs"], [longer])

    def test_joint_payload_counts_programs_before_instructions(self):
        data = quality.payload(self.puzzle, [self.original, self.original], joint=True).split()
        self.assertEqual(data[:8], ["puzzle", "10", "1", "1", "3", "5", "2", "4"])


if __name__ == "__main__":
    unittest.main()
