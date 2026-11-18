import subprocess
import unittest
from unittest.mock import patch

from santa2026.beam import polish_beam, solve_beam
from santa2026.core import Puzzle


class BeamInterfaceTests(unittest.TestCase):
    def test_line_protocol_does_not_depend_on_puzzle_id(self):
        puzzle = Puzzle("an id with spaces", (3,), (1,), 3)
        completed = subprocess.CompletedProcess([], 0, 'puzzle [["/",0,0,1]]\n', '')
        with patch("santa2026.beam.subprocess.run", return_value=completed) as call:
            program = solve_beam(puzzle)
        self.assertEqual(len(program), 1)
        self.assertTrue(call.call_args.kwargs["input"].startswith("puzzle 3 1 1 3 1"))

    def test_polish_rejects_regression_and_preserves_input(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        original = [["/", 0, 0, 1]]
        completed = subprocess.CompletedProcess([], 0, 'puzzle [["/",0,0,1],["+",1,1,2]]\n', '')
        with patch("santa2026.beam.subprocess.run", return_value=completed) as call:
            program = polish_beam(puzzle, original)
        self.assertEqual(program, original)
        self.assertIsNot(program, original)
        self.assertIn("6", call.call_args.args[0])
        self.assertIn("\n1\n/ 0 0 1\n", call.call_args.kwargs["input"])

    def test_invalid_compiled_output_is_rejected(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        completed = subprocess.CompletedProcess([], 0, 'puzzle []\n', '')
        with patch("santa2026.beam.subprocess.run", return_value=completed):
            with self.assertRaises(ValueError):
                solve_beam(puzzle)


if __name__ == "__main__":
    unittest.main()
