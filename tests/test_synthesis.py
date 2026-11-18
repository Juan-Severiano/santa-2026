import random
import unittest

from santa2026.core import Puzzle, validate_program
from santa2026.solver import solve
from santa2026.synthesis import _synthesize, solve_synthesis


class SynthesisTests(unittest.TestCase):
    def check_candidate(self, puzzle, **options):
        candidate = solve_synthesis(puzzle, **options)
        validate_program(puzzle, candidate)
        self.assertLessEqual(len(candidate), len(solve(puzzle, strategy="window")))
        return candidate

    def test_free_large_seed_is_used_as_multiplier(self):
        puzzle = Puzzle("free_base", (631, 889, 1205, 1563), (1563**3 + 7, 1563**4 + 9), 10**15)
        candidate = self.check_candidate(puzzle)
        self.assertLess(len(candidate), len(solve(puzzle, strategy="window")))

    def test_duplicate_initial_values_and_existing_targets(self):
        self.assertEqual(solve_synthesis(Puzzle("done", (3, 3), (3,), 3)), [])
        self.check_candidate(Puzzle("duplicates", (3, 3), (1, 2, 5, 8), 9))

    def test_tight_positive_integer_bounds(self):
        for limit in (1, 2, 3, 15, 31, 63, 127, 1023, 65535):
            with self.subTest(limit=limit):
                self.check_candidate(Puzzle("bound", (limit,), (1, max(1, limit - 1), limit), limit))

    def test_register_recycling_and_seed_schedules_are_deterministic(self):
        targets = tuple(10**18 - 1000000007 * index for index in range(32))
        puzzle = Puzzle("recycle", (631, 889, 1205, 1563), targets, 10**18)
        for seed in (0, 1, 2, 3):
            with self.subTest(seed=seed):
                first = self.check_candidate(puzzle, seed=seed)
                self.assertEqual(first, solve_synthesis(puzzle, seed=seed))

    def test_raw_constructor_survives_random_leaf_recycling(self):
        random_source = random.Random(20261118)
        for index in range(6):
            starts = tuple(random_source.randint(100, 2000) for _ in range(8))
            targets = tuple(sorted(random_source.sample(range(1, 10**15), 24)))
            puzzle = Puzzle(f"random_{index}", starts, targets, 10**15)
            with self.subTest(index=index):
                validate_program(puzzle, _synthesize(puzzle, index, 1))

    def test_deeper_search_still_produces_legal_program(self):
        puzzle = Puzzle("deeper", (101, 127), (101**4 + 7, 127**4 - 3), 10**12)
        self.check_candidate(puzzle, effort=2)

    def test_invalid_search_parameters_are_rejected(self):
        puzzle = Puzzle("parameters", (3,), (1,), 3)
        for seed, effort in ((0, 0), (0, -1), (0, 1.5), ("0", 1), (True, 1)):
            with self.subTest(seed=seed, effort=effort), self.assertRaises(ValueError):
                solve_synthesis(puzzle, seed=seed, effort=effort)


if __name__ == "__main__":
    unittest.main()
