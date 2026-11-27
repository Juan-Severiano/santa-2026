import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle, validate_program
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

    def test_unknown_feature_bit_is_rejected_before_native_execution(self):
        with patch.dict(os.environ, {"SANTA2026_LNS_FLAGS": "256"}), \
                patch("santa2026.rust_lns.subprocess.run") as run:
            with self.assertRaises(ValueError):
                optimize_rust_lns(self.puzzle, self.original)
        run.assert_not_called()


class RustLnsFrontierIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        compiler = shutil.which("rustc")
        if compiler is None:
            found = list((root / "outputs/toolchains/rustup/toolchains").glob("stable-*/bin/rustc"))
            compiler = str(found[0]) if len(found) == 1 else None
        if compiler is None:
            raise unittest.SkipTest("A Rust compiler is required")
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.directory.name) / "lns"
        subprocess.run([compiler, "--edition=2021", "-O", str(root / "rust/lns.rs"),
                        "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_atomic_replacement_emits_a_shorter_program_accepted_by_python(self):
        puzzle = Puzzle("four-ports", (3, 5, 7, 11, 14, 28, 41, 43, 47, 53),
                        (67, 69, 71, 75), 1000)
        original = [["-", 2, 0, 10], ["*", 10, 10, 11], ["*", 11, 10, 12],
                    ["+", 12, 0, 13], ["+", 12, 1, 14], ["+", 12, 2, 15], ["+", 12, 3, 16]]
        for flags in (16, 20, 31, 48, 80, 112, 176, 240):
            with self.subTest(flags=flags), patch.dict(os.environ, {
                    "SANTA2026_LNS_BINARY": str(self.binary),
                    "SANTA2026_LNS_FLAGS": str(flags), "SANTA2026_LNS_MS": "250"}):
                result = optimize_rust_lns(puzzle, original)
            validate_program(puzzle, result)
            self.assertEqual(len(result), 4)
        self.assertEqual(len(original), 7)

    def test_full_start_bank_keeps_legal_overwrite(self):
        puzzle = Puzzle("full-bank", tuple(range(1, 49)), (49,), 100)
        original = [["+", 0, 47, 0]]
        with patch.dict(os.environ, {"SANTA2026_LNS_BINARY": str(self.binary),
                                    "SANTA2026_LNS_FLAGS": "16", "SANTA2026_LNS_MS": "50"}):
            result = optimize_rust_lns(puzzle, original)
        validate_program(puzzle, result)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
