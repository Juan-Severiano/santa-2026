import random
import unittest

from santa2026.core import Puzzle, validate_program
from santa2026.optimizer import optimize_program
from santa2026.solver import solve


class OptimizerTests(unittest.TestCase):
    def test_removes_dead_setup_and_instructions_after_coverage(self):
        puzzle = Puzzle("dead", (3,), (6,), 100)
        original = [["/", 0, 0, 1], ["+", 1, 1, 2], ["+", 0, 0, 3], ["*", 2, 2, 4]]
        result = optimize_program(puzzle, original)
        self.assertEqual(len(result), 1)
        validate_program(puzzle, result)
        self.assertEqual(len(original), 4)

    def test_target_coverage_survives_destruction_of_registers(self):
        puzzle = Puzzle("coverage", (3,), (1, 2, 5, 8), 9)
        original = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3], ["+", 0, 3, 4]]
        self.assertEqual(len(optimize_program(puzzle, original)), 4)

    def test_semantic_rewrite_removes_unneeded_intermediate_chain(self):
        puzzle = Puzzle("rewrite", (2, 3, 7), (14, 21), 100)
        original = [["+", 0, 1, 3], ["+", 3, 0, 4], ["+", 4, 4, 5], ["*", 1, 2, 6]]
        result = optimize_program(puzzle, original)
        self.assertEqual(len(result), 2)
        validate_program(puzzle, result)

    def test_equal_values_are_shared_when_allocation_allows(self):
        puzzle = Puzzle("sharing", (2, 3), (10, 15), 100)
        original = [["+", 0, 1, 2], ["*", 2, 0, 3], ["+", 0, 1, 4], ["*", 4, 1, 5]]
        self.assertEqual(len(optimize_program(puzzle, original)), 3)

    def test_two_operation_replacement_cuts_a_longer_target_recipe(self):
        puzzle = Puzzle("two_steps", (2, 3, 7, 11), (25, 33, 77), 100)
        original = [
            ["+", 2, 3, 4], ["+", 4, 1, 5], ["+", 5, 0, 6],
            ["+", 6, 0, 7], ["*", 1, 3, 8], ["*", 2, 3, 9],
        ]
        result = optimize_program(puzzle, original, seed=0, effort=1)
        self.assertEqual(len(result), 4)
        validate_program(puzzle, result)

    def test_register_pressure_falls_back_without_regression(self):
        puzzle = Puzzle("few", (3,), (1, 2, 5, 8), 9, registers=3)
        original = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 2], ["+", 0, 2, 2]]
        result = optimize_program(puzzle, original, seed=9, effort=2)
        self.assertLessEqual(len(result), len(original))
        validate_program(puzzle, result)

    def test_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            optimize_program(Puzzle("bad", (3,), (6,), 9), [["+", 0, 1, 0]])

    def test_seeded_large_programs_validate_and_are_deterministic(self):
        rng = random.Random(1126)
        for index in range(4):
            puzzle = Puzzle(str(index), (3, 5, 7), tuple(rng.sample(range(100, 10**10), 10)), 10**12)
            original = solve(puzzle, "window")
            result = optimize_program(puzzle, original, seed=21)
            self.assertLessEqual(len(result), len(original))
            validate_program(puzzle, result)
            self.assertEqual(result, optimize_program(puzzle, original, seed=21))

    def test_empty_program_and_initial_targets(self):
        self.assertEqual(optimize_program(Puzzle("done", (3,), (3,), 3), []), [])


if __name__ == "__main__":
    unittest.main()
