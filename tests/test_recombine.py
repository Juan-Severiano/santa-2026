import copy
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle, validate_program
from santa2026.recombine import _cross_recipes, _make_model, _union, optimize_recombine, optimize_recombine_cross

try:
    from ortools.sat.python import cp_model
except ImportError:
    cp_model = None


class RecombineTests(unittest.TestCase):
    def choices(self):
        puzzle = Puzzle("complementary", (2, 3), (9, 10), 10)
        first = [["*", 1, 1, 2], ["+", 0, 0, 3], ["+", 3, 3, 4], ["+", 4, 0, 5]]
        second = [["+", 0, 1, 2], ["+", 2, 2, 3], ["+", 1, 1, 4], ["+", 4, 1, 5]]
        return puzzle, [first, second]

    @unittest.skipIf(cp_model is None, "optional OR-Tools is not installed")
    def test_union_beats_each_valid_input(self):
        puzzle, programs = self.choices()
        result = optimize_recombine(puzzle, programs)
        validate_program(puzzle, result)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(len(result) < len(program) for program in programs))

    @unittest.skipIf(cp_model is None, "optional OR-Tools is not installed")
    def test_cross_edges_find_a_route_never_observed_in_any_input(self):
        puzzle = Puzzle("cross-edges", (2, 5), (8, 10), 10)
        original = [["+", 0, 0, 2], ["+", 2, 2, 3], ["+", 3, 0, 4]]
        self.assertEqual(len(optimize_recombine(puzzle, [original])), 3)
        result = optimize_recombine_cross(puzzle, [original])
        validate_program(puzzle, result)
        self.assertEqual(len(result), 2)
        self.assertEqual(len(original), 3)

    @unittest.skipIf(cp_model is None, "optional OR-Tools is not installed")
    def test_cross_edges_still_forbid_circular_derivations(self):
        puzzle = Puzzle("cross-cycle", (2, 5), (8, 10), 10)
        original = [["+", 0, 0, 2], ["+", 2, 2, 3], ["+", 3, 0, 4]]
        recipes, _, warm, levels = _union(puzzle, [original])
        recipes = _cross_recipes(puzzle, recipes)
        model, _, variables, _ = _make_model(puzzle, recipes, warm, levels, cp_model)
        model.add(variables[8, ("-", 10, 2)] == 1)
        model.add(variables[10, ("+", 2, 8)] == 1)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 1
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.solve(model), cp_model.INFEASIBLE)

    @unittest.skipIf(cp_model is None, "optional OR-Tools is not installed")
    def test_union_cycles_cannot_be_selected(self):
        puzzle = Puzzle("cyclic-alternatives", (1, 2, 4), (3, 7, 10), 10)
        first = [["+", 0, 1, 3], ["+", 3, 2, 4], ["+", 3, 4, 5]]
        second = [["+", 1, 2, 3], ["+", 3, 0, 4], ["-", 4, 2, 5], ["+", 4, 5, 6]]
        for program in (first, second):
            validate_program(puzzle, program)
        recipes, first_seen, warm, levels = _union(puzzle, [first, second])
        model, chosen, variables, ranks = _make_model(puzzle, recipes, warm, levels, cp_model)
        model.add(variables[3, ("-", 7, 4)] == 1)
        model.add(variables[7, ("+", 3, 4)] == 1)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 1
        solver.parameters.num_search_workers = 1
        self.assertEqual(solver.solve(model), cp_model.INFEASIBLE)

    def test_input_programs_are_not_modified(self):
        puzzle, programs = self.choices()
        before = copy.deepcopy(programs)
        result = optimize_recombine(puzzle, programs, seed=3)
        self.assertEqual(programs, before)
        self.assertIsNot(result, programs[0])
        validate_program(puzzle, result)

    @unittest.skipIf(cp_model is None, "optional OR-Tools is not installed")
    def test_duplicate_initial_values_preserve_register_positions(self):
        puzzle = Puzzle("duplicate-starts", (2, 2, 3), (9, 10), 10)
        first = [["*", 2, 2, 3], ["+", 0, 1, 4], ["+", 4, 4, 5], ["+", 5, 0, 6]]
        second = [["+", 1, 2, 3], ["+", 3, 3, 4], ["+", 2, 2, 5], ["+", 5, 2, 6]]
        result = optimize_recombine(puzzle, [first, second])
        self.assertEqual(len(result), 3)
        validate_program(puzzle, result)

    def test_shortest_candidate_is_fallback_without_ortools(self):
        puzzle, programs = self.choices()
        programs.append(programs[0] + [["/", 0, 0, 6]])
        with patch.dict("sys.modules", {"ortools.sat.python": None}):
            result = optimize_recombine(puzzle, programs)
        self.assertEqual(result, programs[0])
        self.assertIsNot(result, programs[0])

    def test_duplicates_and_initial_targets(self):
        puzzle = Puzzle("already-done", (3, 3), (3,), 3)
        self.assertEqual(optimize_recombine(puzzle, [[]]), [])

    def test_invalid_input_and_parameters_are_rejected(self):
        puzzle, programs = self.choices()
        for bad in ([], [None], [[]]):
            with self.subTest(programs=bad), self.assertRaises(ValueError):
                optimize_recombine(puzzle, bad)
        with self.assertRaises(ValueError):
            optimize_recombine(puzzle, programs, effort=0)
        with self.assertRaises(ValueError):
            optimize_recombine(puzzle, programs, seed=True)


if __name__ == "__main__":
    unittest.main()
