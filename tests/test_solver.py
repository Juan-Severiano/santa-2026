import random
import unittest

from santa2026.core import Puzzle, validate_program
from santa2026.solver import _solve_window, solve, solve_radix


class SolverTests(unittest.TestCase):
    def check_solution(self, puzzle, strategy="portfolio"):
        program = solve(puzzle, strategy=strategy)
        result = validate_program(puzzle, program)
        self.assertEqual(result.covered_targets, len(set(puzzle.targets)))
        return program

    def test_worked_example_matches_four_instruction_lower_bound(self):
        program = self.check_solution(Puzzle("toy", (3,), (1, 2, 5, 8), 9))
        self.assertEqual(len(program), 4)

    def test_existing_targets_need_no_work(self):
        self.assertEqual(solve(Puzzle("done", (1, 3, 3), (1, 3), 3)), [])

    def test_only_missing_target_one(self):
        self.assertEqual(len(self.check_solution(Puzzle("one", (3,), (1,), 3))), 1)

    def test_tight_value_limits(self):
        for limit in range(1, 35):
            with self.subTest(limit=limit):
                self.check_solution(Puzzle("tight", (limit,), tuple(range(1, limit + 1)), limit))

    def test_large_targets_and_register_recycling(self):
        targets = tuple(10**18 - 1000003 * index for index in range(32))
        puzzle = Puzzle("large", (3, 3, 7), targets, 10**18)
        program = self.check_solution(puzzle)
        self.assertEqual(program, solve(puzzle))

    def test_seeded_random_puzzles_cover_exact_targets(self):
        random_source = random.Random(2026)
        for index in range(20):
            limit = random_source.randint(10**12, 10**18)
            starts = tuple(random_source.randint(1, 32) for _ in range(random_source.randint(1, 8)))
            targets = tuple(sorted(random_source.sample(range(1, limit), 32)))
            with self.subTest(puzzle=index):
                self.check_solution(Puzzle(f"random_{index}", starts, targets, limit))

    def test_signed_window_overshoot_falls_back_to_valid_radix(self):
        puzzle = Puzzle("signed_bound", (3,), (1023,), 1023)
        for width in (4, 5, 6):
            with self.subTest(width=width), self.assertRaises(ValueError):
                _solve_window(puzzle, width)
        self.assertEqual(self.check_solution(puzzle, "window"), solve_radix(puzzle))

    def test_signed_window_large_programs_replay_exactly(self):
        targets = tuple(10**18 - index * 1000000007 for index in range(32))
        self.check_solution(Puzzle("signed_large", (3, 7, 31), targets, 10**18), "window")

    def test_portfolio_preserves_radix_candidate(self):
        puzzle = Puzzle("reuse", (3, 5), (100000000003, 100000000007, 100000000009), 10**12)
        radix = solve_radix(puzzle)
        self.assertEqual(solve(puzzle, "radix"), radix)
        self.assertLessEqual(len(self.check_solution(puzzle)), len(radix))

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(ValueError):
            solve(Puzzle("unknown", (3,), (1,), 3), "typo")


if __name__ == "__main__":
    unittest.main()
