import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from santa2026.core import Puzzle, validate_program


class CompiledMitmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("A C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.directory.name) / "mitm"
        source = Path(__file__).resolve().parents[1] / "cpp" / "mitm_search.cpp"
        subprocess.run([compiler, "-std=c++17", "-O2", str(source), "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def repair(self, puzzle, hint, seed=0, valid=True):
        fields = [puzzle.id, puzzle.value_limit, len(puzzle.starting_values),
                  len(puzzle.targets), *puzzle.starting_values, *puzzle.targets, len(hint)]
        for instruction in hint:
            fields.extend(instruction)
        result = subprocess.run(
            [str(self.binary), "32", "6", str(seed), "3", "20", "16", "1"],
            input=" ".join(map(str, fields)) + "\n", text=True, capture_output=True, timeout=30,
        )
        if not valid:
            self.assertNotEqual(result.returncode, 0)
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        identifier, encoded = result.stdout.strip().split(" ", 1)
        self.assertEqual(identifier, puzzle.id)
        program = json.loads(encoded)
        validate_program(puzzle, program)
        self.assertLessEqual(len(program), len(hint))
        return program

    def test_exact_short_recipes_improve_shared_hub(self):
        puzzle = Puzzle("hub", (3, 5), (24, 36, 60), 100)
        hint = [["/", 0, 0, 2], ["-", 0, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 4, 6], ["+", 6, 6, 7],
                ["+", 7, 6, 8], ["+", 7, 8, 9]]
        self.assertLess(len(self.repair(puzzle, hint)), len(hint))

    def test_exclusive_diamond_is_not_exposed_as_free_leaves(self):
        puzzle = Puzzle("diamond", (3, 5), (36, 60), 100)
        hint = [["/", 0, 0, 2], ["-", 0, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 4, 6], ["+", 6, 6, 7],
                ["+", 7, 6, 8], ["+", 8, 7, 9]]
        self.assertLess(len(self.repair(puzzle, hint)), len(hint))

    def test_shared_square_of_one_operation_value_costs_two(self):
        puzzle = Puzzle("square", (3, 5), (64,), 100)
        hint = [["/", 0, 0, 2], ["-", 0, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 5, 6], ["+", 6, 6, 7],
                ["+", 7, 7, 8]]
        self.assertLessEqual(len(self.repair(puzzle, hint)), 2)

    def test_two_plus_two_join_finds_five_operation_product(self):
        puzzle = Puzzle("balanced", (2, 3), (77,), 100)
        hint = [["+", 0, 0, 2], ["+", 2, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 5, 6], ["+", 3, 2, 7],
                ["+", 6, 7, 8], ["/", 1, 1, 9], ["+", 8, 9, 10]]
        self.assertLessEqual(len(self.repair(puzzle, hint)), 5)

    def test_tight_limit_and_descendants_remain_exact(self):
        puzzle = Puzzle("tight", (3,), (5, 10, 15, 25), 25)
        hint = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3],
                ["+", 3, 3, 4], ["+", 4, 3, 5], ["+", 5, 4, 6]]
        for seed in (0, 1, 2):
            with self.subTest(seed=seed):
                self.repair(puzzle, hint, seed)

    def test_preserves_hint_with_all_seed_registers_protected(self):
        puzzle = Puzzle("full", tuple(range(1, 49)), (49,), 100)
        hint = [["+", 0, 47, 0]]
        self.assertEqual(self.repair(puzzle, hint), hint)

    def test_rejects_illegal_arithmetic_and_missing_targets(self):
        self.repair(Puzzle("division", (3, 2), (1,), 10), [["/", 0, 1, 2]], valid=False)
        self.repair(Puzzle("missing", (3,), (5,), 10), [], valid=False)

    def test_initially_covered_targets_require_no_program(self):
        self.assertEqual(self.repair(Puzzle("covered", (1,), (1,), 1), []), [])


if __name__ == "__main__":
    unittest.main()
