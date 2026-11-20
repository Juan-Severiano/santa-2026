import copy
import subprocess
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle
from santa2026.rust_lns import optimize_rust_lns


class RustLnsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = Puzzle("recovery", (2, 5), (10,), 10)
        self.original = [["+", 0, 0, 2], ["+", 1, 1, 3]]
        self.shorter = [["*", 0, 1, 2]]

    def test_recovers_complete_replayed_output_after_interruption(self):
        encoded = b'puzzle [["*",0,1,2]]\n'
        for error in (subprocess.TimeoutExpired("lns", 1, output=encoded + b"partial"),
                      subprocess.CalledProcessError(1, "lns", output=encoded)):
            with self.subTest(kind=type(error).__name__), patch("santa2026.rust_lns.subprocess.run", side_effect=error):
                self.assertEqual(optimize_rust_lns(self.puzzle, self.original), self.shorter)

    def test_invalid_or_partial_output_preserves_process_error(self):
        for output in (None, b'puzzle [["*",0,1,2]]', b'puzzle []\n', b'puzzle invalid\n'):
            error = subprocess.TimeoutExpired("lns", 1, output=output)
            with self.subTest(output=output), patch("santa2026.rust_lns.subprocess.run", side_effect=error):
                with self.assertRaises(subprocess.TimeoutExpired):
                    optimize_rust_lns(self.puzzle, self.original)

    def test_longer_valid_trajectory_never_replaces_input(self):
        before = copy.deepcopy(self.original)
        output = 'puzzle [["+",0,0,2],["+",1,1,3],["+",0,0,4]]\n'
        with patch("santa2026.rust_lns.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            result = optimize_rust_lns(self.puzzle, self.original)
        self.assertEqual(result, before)
        self.assertEqual(self.original, before)
        self.assertIsNot(result, self.original)

    def test_rejects_inexact_division_even_if_result_looks_covered(self):
        output = 'puzzle [["/",1,0,2],["*",2,1,3]]\n'
        with patch("santa2026.rust_lns.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            with self.assertRaises(ValueError):
                optimize_rust_lns(self.puzzle, self.original)


if __name__ == "__main__":
    unittest.main()
