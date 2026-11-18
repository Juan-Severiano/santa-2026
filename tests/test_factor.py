import random
import unittest

from santa2026.core import Puzzle, validate_program
from santa2026.factor import _factor_pairs, _is_prime, solve_factor
from santa2026.solver import solve


class FactorTests(unittest.TestCase):
    def test_prime_test_known_64_bit_cases(self):
        for value in (2, 3, 97, 257, 65537, 1000000007, 2305843009213693951):
            self.assertTrue(_is_prime(value), value)
        for value in (0, 1, 4, 341, 561, 3215031751, 1000000007 * 1000000009):
            self.assertFalse(_is_prime(value), value)

    def test_pairs_are_exact_including_balanced_products(self):
        for value in (64, 360, 65537 * 257, 1009 * 1013, 1000000007 * 1000000009):
            for left, right in _factor_pairs(value, 2):
                self.assertEqual(left * right, value)
                self.assertGreater(left, 1)
        self.assertIn((1009, 1013), _factor_pairs(1009 * 1013, 2))

    def test_seed_values_and_tight_limits(self):
        for limit in (1, 3, 17, 31, 63):
            puzzle = Puzzle("tight", (limit,), tuple(range(1, limit + 1)), limit)
            result = solve_factor(puzzle)
            validate_program(puzzle, result)

    def test_overshoot_and_register_recycling(self):
        puzzle = Puzzle("bound", (3, 7, 31), (1023, 65535, 999999999999999999), 10**18 - 1)
        result = solve_factor(puzzle)
        validate_program(puzzle, result)
        self.assertLessEqual(len(result), len(solve(puzzle, "window")))

    def test_large_deterministic_candidates(self):
        rng = random.Random(20261118)
        puzzle = Puzzle("large", (3, 5, 11), tuple(rng.sample(range(10**12, 10**16), 8)), 10**18)
        result = solve_factor(puzzle, seed=2)
        validate_program(puzzle, result)
        self.assertEqual(result, solve_factor(puzzle, seed=2))

    def test_rejects_invalid_search_parameters(self):
        puzzle = Puzzle("params", (3,), (1,), 3)
        for seed, effort in ((True, 1), (0, 0), (0, 1.5)):
            with self.assertRaises(ValueError):
                solve_factor(puzzle, seed, effort)


if __name__ == "__main__":
    unittest.main()
