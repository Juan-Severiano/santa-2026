import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle, validate_program
from santa2026.rust_window import optimize_rust_window


ROOT = Path(__file__).resolve().parents[1]


class RustWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("rustc")
        if compiler is None:
            matches = sorted((ROOT / "outputs/toolchains/rustup/toolchains").glob("stable-*/bin/rustc"))
            compiler = str(matches[0]) if len(matches) == 1 else None
        if compiler is None:
            raise unittest.SkipTest("A Rust compiler is required")
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.directory.name) / "window"
        subprocess.run([compiler, "--edition=2021", "-O", str(ROOT / "rust/window.rs"), "-o", str(cls.binary)], check=True)
        cls.unit = Path(cls.directory.name) / "window-unit"
        subprocess.run([compiler, "--edition=2021", "--test", "-O", str(ROOT / "rust/window.rs"), "-o", str(cls.unit)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def optimize(self, puzzle, program, rename=False):
        before = copy.deepcopy(program)
        with patch.dict("os.environ", {"SANTA2026_WINDOW_BINARY": str(self.binary),
                                       "SANTA2026_WINDOW_RENAME": "1" if rename else "0"}):
            result = optimize_rust_window(puzzle, program)
        validate_program(puzzle, result)
        self.assertEqual(program, before)
        self.assertLessEqual(len(result), len(program))
        return result

    def test_shared_latent_helper_improves_two_outputs_together(self):
        puzzle = Puzzle("shared", (2, 5), (14, 35), 100)
        program = [["+", 0, 0, 2], ["+", 1, 1, 3], ["+", 3, 2, 4],
                   ["*", 1, 1, 5], ["+", 5, 3, 6]]
        self.assertEqual(len(self.optimize(puzzle, program)), 3)

    def test_multi_output_window_preserves_exact_suffix_registers(self):
        result = subprocess.run([str(self.unit), "engine::window_tests"], check=True,
                                text=True, capture_output=True, timeout=10)
        self.assertIn("test result: ok", result.stdout)
        self.assertIn("renaming_allows_a_shorter_window", result.stdout)

    def test_target_coverage_survives_overwriting_all_intermediate_registers(self):
        puzzle = Puzzle("overwritten", (2, 5), (4, 7, 10), 100)
        program = [["+", 0, 0, 2], ["+", 0, 1, 2], ["+", 1, 1, 2], ["+", 0, 0, 2]]
        self.assertEqual(len(self.optimize(puzzle, program)), 3)

    def test_suffix_reads_remain_valid_after_multi_output_replacement(self):
        puzzle = Puzzle("suffix", (2, 5), (14, 35, 490), 1000)
        program = [["+", 0, 0, 2], ["+", 1, 1, 3], ["+", 3, 2, 4],
                   ["*", 1, 1, 5], ["+", 5, 3, 6], ["*", 4, 6, 7]]
        self.assertEqual(len(self.optimize(puzzle, program)), 4)

    def test_full_48_registers_and_legal_seed_overwrite(self):
        puzzle = Puzzle("full", tuple(range(1, 49)), (49, 50, 51), 100)
        for rename in (False, True):
            self.optimize(puzzle, [["+", 0, 47, 0], ["+", 1, 47, 1], ["+", 2, 47, 2]], rename=rename)
            self.assertEqual(len(self.optimize(Puzzle("full-shorter", tuple(range(1, 49)), (99,), 100),
                                             [["+", 47, 47, 47], ["+", 47, 1, 47], ["+", 47, 0, 47]], rename=rename)), 1)

    def test_maximum_products_and_exact_division(self):
        puzzle = Puzzle("maximum", (10**9, 10**9 - 1), (10**18, 10**18 - 10**9, 1), 10**18)
        for rename in (False, True):
            self.optimize(puzzle, [["*", 0, 0, 2], ["*", 0, 1, 3], ["-", 0, 1, 4]], rename=rename)

    def test_invalid_hint_is_rejected_before_search(self):
        with self.assertRaises(ValueError):
            self.optimize(Puzzle("invalid", (3, 2), (1,), 10), [["/", 0, 1, 2]])

    def test_renamed_suffix_program_passes_python_integer_replay(self):
        puzzle = Puzzle("renamed", (2, 5), (10, 12, 24), 100)
        program = [["/", 0, 0, 2], ["*", 0, 2, 3], ["*", 1, 2, 4],
                   ["*", 3, 4, 6], ["+", 6, 3, 4], ["*", 4, 3, 6]]
        self.assertEqual(len(self.optimize(puzzle, program, rename=True)), 3)

    def test_rename_retains_targets_that_were_overwritten_inside_the_window(self):
        puzzle = Puzzle("renamed-covered", (2, 5), (4, 7, 10), 100)
        program = [["+", 0, 0, 2], ["+", 0, 1, 2], ["+", 1, 1, 2], ["+", 0, 0, 2]]
        self.assertEqual(len(self.optimize(puzzle, program, rename=True)), 3)

    def test_invalid_rename_flag_is_rejected_before_native_execution(self):
        with patch.dict("os.environ", {"SANTA2026_WINDOW_RENAME": "yes"}):
            with self.assertRaisesRegex(ValueError, "rename"):
                optimize_rust_window(Puzzle("flag", (2, 5), (7,), 10), [["+", 0, 1, 2]])


if __name__ == "__main__":
    unittest.main()
