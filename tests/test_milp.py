import importlib.util
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle, validate_program
from santa2026.milp import _problem, optimize_milp


class MilpTests(unittest.TestCase):
    def test_input_validation_and_parameters(self):
        puzzle = Puzzle("bad", (3,), (6,), 9)
        with self.assertRaises(ValueError):
            optimize_milp(puzzle, [["+", 0, 1, 0]])
        for seed, effort in ((True, 1), (0, 0), (0, 1.5)):
            with self.assertRaises(ValueError):
                optimize_milp(puzzle, [["+", 0, 0, 0]], seed, effort)

    def test_optional_solver_failure_retains_input_without_mutation(self):
        puzzle = Puzzle("fallback", (3,), (6,), 9)
        program = [["+", 0, 0, 0]]
        with patch("santa2026.milp._solve_model", side_effect=ImportError("optional")):
            result = optimize_milp(puzzle, program)
        self.assertEqual(result, program)
        self.assertIsNot(result, program)

    def test_height_order_accepts_original_recipes(self):
        puzzle = Puzzle("model", (2, 3), (10, 15), 100)
        program = [["+", 0, 1, 2], ["*", 2, 0, 3], ["*", 2, 1, 4]]
        for seed in range(4):
            _, nonseeds, recipes, _, ranks, unrestricted = _problem(puzzle, program, seed)
            self.assertEqual(set(nonseeds), {5, 10, 15})
            self.assertIn(("+", 2, 3), recipes[5])
            if not unrestricted:
                for result, choices in recipes.items():
                    for _, left, right in choices:
                        self.assertLess(max(ranks[left], ranks[right]), ranks[result])

    @unittest.skipUnless(importlib.util.find_spec("scipy"), "optional SciPy unavailable")
    def test_global_sharing_shortens_valid_program(self):
        puzzle = Puzzle("sharing", (2, 3), (10, 15), 100)
        program = [["+", 0, 1, 2], ["*", 2, 0, 3], ["+", 0, 1, 4], ["*", 4, 1, 5]]
        result = optimize_milp(puzzle, program)
        self.assertEqual(len(result), 3)
        validate_program(puzzle, result)
        self.assertEqual(len(program), 4)

    @unittest.skipUnless(importlib.util.find_spec("scipy"), "optional SciPy unavailable")
    def test_unrestricted_recipe_model_has_no_selected_cycles(self):
        puzzle = Puzzle("cycles", (3,), (1, 2, 5, 8), 9)
        program = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3], ["+", 0, 3, 4]]
        result = optimize_milp(puzzle, program, seed=3)
        self.assertEqual(len(result), 4)
        validate_program(puzzle, result)

    @unittest.skipUnless(importlib.util.find_spec("scipy"), "optional SciPy unavailable")
    def test_new_helper_replaces_a_longer_recipe(self):
        puzzle = Puzzle("new_helper", (2, 3, 7, 11), (25, 33, 77), 100)
        program = [
            ["+", 2, 3, 4], ["+", 4, 1, 5], ["+", 5, 0, 6],
            ["+", 6, 0, 7], ["*", 1, 3, 8], ["*", 2, 3, 9],
        ]
        result = optimize_milp(puzzle, program)
        self.assertEqual(len(result), 4)
        validate_program(puzzle, result)

    def test_initial_targets_need_no_instructions(self):
        self.assertEqual(optimize_milp(Puzzle("done", (3,), (3,), 3), []), [])


if __name__ == "__main__":
    unittest.main()
