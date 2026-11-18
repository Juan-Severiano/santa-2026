import random
import unittest

from santa2026.core import Puzzle, validate_program
from santa2026.shared import _prune, solve_shared


class SharedSolverTests(unittest.TestCase):
    def test_shared_intermediate(self):
        puzzle = Puzzle("shared", (10,), (1001, 2001, 3001, 4001), 5000)
        program = solve_shared(puzzle)
        self.assertLessEqual(len(program), 12)
        self.assertEqual(validate_program(puzzle, program).covered_targets, 4)

    def test_near_multiples_with_signed_residuals(self):
        puzzle = Puzzle("near", (3, 7), (1003, 2001, 3005, 4999), 6000)
        validate_program(puzzle, solve_shared(puzzle, seed=1, effort=2))

    def test_exact_limit_and_small_values(self):
        for limit in (2, 5, 16, 31, 100, 1000, 10**18):
            puzzle = Puzzle("limit", (1,), tuple(sorted({1, limit, max(1, limit - 1)})), limit)
            validate_program(puzzle, solve_shared(puzzle))

    def test_deterministic_and_random_puzzles(self):
        rng = random.Random(42)
        for index in range(12):
            limit = rng.randint(100, 10**8)
            targets = tuple(sorted({rng.randint(1, limit) for _ in range(8)}))
            puzzle = Puzzle(f"random_{index}", (3, 5), targets, limit)
            program = solve_shared(puzzle, seed=index)
            self.assertEqual(program, solve_shared(puzzle, seed=index))
            validate_program(puzzle, program)

    def test_high_powers_and_shared_bank(self):
        puzzle = Puzzle("powers", (3, 7), (2**32 + 1, 2**40 - 1, 2**48 + 1), 2**50)
        for seed in (5, 6, 10, 11):
            validate_program(puzzle, solve_shared(puzzle, seed=seed))

    def test_pruning_preserves_targets_and_recycled_dependencies(self):
        puzzle = Puzzle("prune", (3,), (9, 10), 100)
        program = [["/", 0, 0, 1], ["+", 1, 1, 2], ["*", 0, 0, 0], ["+", 0, 1, 2]]
        result = _prune(puzzle, program)
        self.assertEqual(len(result), 3)
        validate_program(puzzle, result)

    def test_bad_search_parameters(self):
        puzzle = Puzzle("parameters", (3,), (9,), 10)
        for kwargs in ({"seed": True}, {"effort": 0}, {"effort": 1.5}):
            with self.assertRaises(ValueError):
                solve_shared(puzzle, **kwargs)


if __name__ == "__main__":
    unittest.main()
