import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from santa2026.beam import binary_path, polish_beam, solve_beam
from santa2026.core import Puzzle


class BeamInterfaceTests(unittest.TestCase):
    def test_default_repair_path_matches_native_build_output(self):
        with patch.dict(os.environ, {}, clear=True), patch("santa2026.beam.DEFAULT_REPAIR_ENGINE", "cpp"):
            self.assertEqual(binary_path(repair=True).name, "repair-cpp")

    def test_repair_override_is_separate_from_construction_engine(self):
        with patch.dict(os.environ, {"SANTA2026_BEAM_BINARY": "/tmp/construction",
                                    "SANTA2026_REPAIR_BINARY": "/tmp/repair"}):
            self.assertEqual(binary_path(), Path("/tmp/construction").resolve())
            self.assertEqual(binary_path(repair=True), Path("/tmp/repair").resolve())

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

    def test_complete_timeout_output_is_recovered_and_replayed(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        failure = subprocess.TimeoutExpired("engine", 1, output=b'puzzle [["/",0,0,1]]\npuzzle [\xf0\x9f')
        with patch("santa2026.beam.subprocess.run", side_effect=failure):
            self.assertEqual(solve_beam(puzzle), [["/", 0, 0, 1]])

    def test_complete_nonzero_exit_output_is_recovered(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        failure = subprocess.CalledProcessError(1, "engine", output='puzzle [["/",0,0,1]]\n', stderr="cleanup failed")
        with patch("santa2026.beam.subprocess.run", side_effect=failure):
            self.assertEqual(solve_beam(puzzle), [["/", 0, 0, 1]])

    def test_partial_invalid_or_duplicate_output_preserves_original_process_failure(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        outputs = (None, 'puzzle [["/",0,0,1]]', 'puzzle []\n', 'puzzle [\n',
                   'wrong [["/",0,0,1]]\n', 'puzzle [["/",0,0,1]]\n' * 2)
        for failure_type in (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            for output in outputs:
                failure = (failure_type("engine", 1, output=output)
                           if failure_type is subprocess.TimeoutExpired
                           else failure_type(1, "engine", output=output))
                with self.subTest(failure=failure), patch("santa2026.beam.subprocess.run", side_effect=failure):
                    with self.assertRaises(failure_type) as caught:
                        solve_beam(puzzle)
                    self.assertIs(caught.exception, failure)

    def test_polish_keeps_input_when_recovered_output_is_longer(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        original = [["/", 0, 0, 1]]
        failure = subprocess.TimeoutExpired("engine", 1, output='puzzle [["/",0,0,1],["+",1,1,2]]\n')
        with patch("santa2026.beam.subprocess.run", side_effect=failure):
            result = polish_beam(puzzle, original)
        self.assertEqual(result, original)
        self.assertIsNot(result, original)


if __name__ == "__main__":
    unittest.main()
