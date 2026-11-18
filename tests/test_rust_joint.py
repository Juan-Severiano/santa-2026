import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle, validate_program
from santa2026.rust_joint import optimize_rust_joint


ROOT = Path(__file__).resolve().parents[1]


class RustJointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("rustc")
        if compiler is None:
            available = sorted((ROOT / "outputs/toolchains/rustup/toolchains").glob("stable-*/bin/rustc"))
            compiler = str(available[0]) if len(available) == 1 else None
        if compiler is None:
            raise unittest.SkipTest("A Rust compiler is required")
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.directory.name) / "joint"
        subprocess.run([compiler, "--edition=2021", "-O", str(ROOT / "rust/joint.rs"), "-o", str(cls.binary)], check=True)
        cls.unit_binary = Path(cls.directory.name) / "joint-unit"
        subprocess.run([compiler, "--edition=2021", "--test", "-O", str(ROOT / "rust/joint.rs"), "-o", str(cls.unit_binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def optimize(self, puzzle, programs, seed=0, helpers=0):
        before = copy.deepcopy(programs)
        with patch.dict("os.environ", {"SANTA2026_JOINT_BINARY": str(self.binary),
                                       "SANTA2026_JOINT_HELPERS": str(helpers)}):
            result = optimize_rust_joint(puzzle, programs, seed=seed)
        self.assertEqual(programs, before)
        validate_program(puzzle, result)
        self.assertLessEqual(len(result), min(map(len, programs)))
        return result

    def test_complementary_programs_share_shorter_recipes(self):
        puzzle = Puzzle("complementary", (2, 3), (9, 10), 10)
        first = [["*", 1, 1, 2], ["+", 0, 0, 3], ["+", 3, 3, 4], ["+", 4, 0, 5]]
        second = [["+", 0, 1, 2], ["+", 2, 2, 3], ["+", 1, 1, 4], ["+", 4, 1, 5]]
        self.assertEqual(len(self.optimize(puzzle, [first, second])), 3)

    def test_generated_recipes_can_skip_old_exclusive_chain(self):
        puzzle = Puzzle("generated", (3, 5), (64,), 100)
        hint = [["/", 0, 0, 2], ["-", 0, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 5, 6], ["+", 6, 6, 7], ["+", 7, 7, 8], ["+", 0, 1, 9]]
        self.assertLessEqual(len(self.optimize(puzzle, [hint])), 2)

    def test_coordinated_switch_escapes_single_recipe_local_minimum(self):
        # Replacing 10=8+2 with 10=5+5 alone leaves the cost at three.
        # Replacing 8=4+4 with 8=10-2 alone creates a cycle. Both switches
        # together remove the helper4 and produce the two required outputs.
        puzzle = Puzzle("joint-minimum", (2, 5), (8, 10), 10)
        hint = [["+", 0, 0, 2], ["+", 2, 2, 3], ["+", 3, 0, 4]]
        self.assertEqual(len(self.optimize(puzzle, [hint])), 2)

    def test_fresh_helper_is_charged_once_and_shared_by_two_outputs(self):
        # Value7 never occurs in this hint. Constructing7=2+5 permits
        # outputs14=7*2 and35=7*5, including the helper's real cost.
        puzzle = Puzzle("fresh-helper", (2, 5), (14, 35), 100)
        hint = [["+", 0, 0, 2], ["+", 1, 1, 3], ["+", 3, 2, 4],
                ["*", 1, 1, 5], ["+", 5, 3, 6]]
        self.assertEqual(len(self.optimize(puzzle, [hint], helpers=0)), 5)
        self.assertEqual(len(self.optimize(puzzle, [hint], helpers=24)), 3)

    def run_native(self, puzzle, programs, evaluations, milliseconds, helpers):
        fields = ["puzzle", puzzle.value_limit, len(puzzle.starting_values), len(puzzle.targets),
                  *puzzle.starting_values, *puzzle.targets, len(programs)]
        for program in programs:
            fields.append(len(program))
            for instruction in program:
                fields.extend(instruction)
        response = subprocess.run(
            [str(self.binary), "0", str(evaluations), "8", "4096", str(milliseconds), str(helpers)],
            input=" ".join(map(str, fields)) + "\n", text=True, capture_output=True, check=True,
            timeout=10, env={**os.environ, "SANTA2026_JOINT_TRACE": "1"},
        )
        identifier, encoded = response.stdout.strip().split(" ", 1)
        self.assertEqual(identifier, "puzzle")
        result = json.loads(encoded)
        validate_program(puzzle, result)
        return result, response.stderr

    def test_directed_macro_works_with_one_evaluation_and_no_random_moves(self):
        puzzle = Puzzle("macro", (2, 5), (14, 35), 100)
        hint = [["+", 0, 0, 2], ["+", 1, 1, 3], ["+", 3, 2, 4],
                ["*", 1, 1, 5], ["+", 5, 3, 6]]
        result, trace = self.run_native(puzzle, [hint], evaluations=1, milliseconds=2000, helpers=24)
        self.assertEqual(len(result), 3)
        self.assertIn("macroeval=1", trace)
        self.assertIn("mutations=0", trace)
        self.assertIn("improvements=1", trace)

    def test_expired_deadline_replays_fallback_and_rejects_invalid_hint(self):
        # An injected expired Instant gives deterministic coverage of the
        # cancellation branch without asserting timing on a loaded machine.
        response = subprocess.run([str(self.unit_binary), "engine::joint_deadline_tests"],
                                  text=True, capture_output=True, check=True, timeout=10)
        self.assertIn("2 passed", response.stdout)

    def test_one_millisecond_budget_preserves_large_valid_hint(self):
        hint = [["+", 0, 0, 1]] + [["+", 1, 0, 1] for _ in range(1199)]
        puzzle = Puzzle("small-budget", (1,), (1201,), 10**6)
        result, _ = self.run_native(puzzle, [hint] * 8, evaluations=100_000, milliseconds=1, helpers=24)
        self.assertLessEqual(len(result), len(hint))

    def test_cyclic_alternatives_are_never_emitted(self):
        puzzle = Puzzle("cycles", (1, 2, 4), (3, 7, 10), 10)
        first = [["+", 0, 1, 3], ["+", 3, 2, 4], ["+", 3, 4, 5]]
        second = [["+", 1, 2, 3], ["+", 3, 0, 4], ["-", 4, 2, 5], ["+", 4, 5, 6]]
        for seed in (0, 1, 2):
            self.optimize(puzzle, [first, second], seed)

    def test_full_seed_bank_keeps_legal_overwrite_fallback(self):
        hint = [["+", 0, 47, 0]]
        self.assertEqual(self.optimize(Puzzle("full", tuple(range(1, 49)), (49,), 100), [hint]), hint)

    def test_maximum_integer_products_and_exact_divisions(self):
        puzzle = Puzzle("maximum", (10**9, 10**9-1), (10**18, 10**18-10**9, 1), 10**18)
        self.optimize(puzzle, [[["*", 0, 0, 2], ["*", 0, 1, 3], ["-", 0, 1, 4]]])

    def test_initial_targets_and_invalid_input(self):
        self.assertEqual(self.optimize(Puzzle("covered", (3, 3), (3,), 3), [[]]), [])
        with self.assertRaises(ValueError):
            optimize_rust_joint(Puzzle("bad", (3,), (1,), 3), [[]])


class RustJointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = Puzzle("recovery", (2, 5), (10,), 10)
        self.original = [["+", 0, 0, 2], ["+", 1, 1, 3]]
        self.shorter = [["*", 0, 1, 2]]

    def test_complete_valid_stdout_is_recovered_after_timeout_or_nonzero_exit(self):
        encoded = 'puzzle [["*",0,1,2]]\n'
        for error in (subprocess.TimeoutExpired(["joint"], 1, output=encoded.encode() + b"partial"),
                      subprocess.CalledProcessError(1, ["joint"], output=encoded)):
            with self.subTest(error=type(error).__name__), patch("santa2026.rust_joint.subprocess.run", side_effect=error):
                self.assertEqual(optimize_rust_joint(self.puzzle, [self.original]), self.shorter)

    def test_incomplete_or_invalid_stdout_preserves_original_process_error(self):
        for output in (None, b'puzzle [["*",0,1,2]]', b'puzzle []\n', b'puzzle invalid\n'):
            error = subprocess.TimeoutExpired(["joint"], 1, output=output)
            with self.subTest(output=output), patch("santa2026.rust_joint.subprocess.run", side_effect=error):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    optimize_rust_joint(self.puzzle, [self.original])
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
