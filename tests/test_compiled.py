import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from santa2026.core import Puzzle, validate_program


class CompiledBeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("A C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.directory.name) / "solver"
        source = Path(__file__).resolve().parents[1] / "cpp" / "beam_search.cpp"
        subprocess.run([compiler, "-std=c++17", "-O2", str(source), "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_solver(self, puzzles, mode=1, seed=0):
        lines = [" ".join(map(str, (puzzle.id, puzzle.value_limit,
                    len(puzzle.starting_values), len(puzzle.targets),
                    *puzzle.starting_values, *puzzle.targets))) for puzzle in puzzles]
        response = subprocess.run(
            [str(self.binary), "2", ".18", "32", "40", "18", str(mode), str(seed)],
            input="\n".join(lines) + "\n", text=True, capture_output=True,
            check=True, timeout=30,
        )
        programs = {}
        for line in response.stdout.splitlines():
            identifier, encoded = line.split(" ", 1)
            programs[identifier] = json.loads(encoded)
        self.assertEqual(set(programs), {puzzle.id for puzzle in puzzles})
        for puzzle in puzzles:
            validate_program(puzzle, programs[puzzle.id])
        return programs

    def test_each_order_mode_replays_exactly(self):
        puzzle = Puzzle("large", (631, 889), (1023007, 1052671, 1111111, 1122334), 10**12)
        for mode in (0, 1, 2, 3, 4, 5):
            with self.subTest(mode=mode):
                self.run_solver([puzzle], mode=mode, seed=7)

    def test_line_protocol_handles_tight_and_covered_puzzles(self):
        self.run_solver([
            Puzzle("covered", (1,), (1,), 1),
            Puzzle("toy", (3,), (1, 2, 5, 8), 9),
            Puzzle("tight", (3, 3), (1, 7, 15), 15),
        ])

    def test_order_seed_is_deterministic(self):
        puzzle = Puzzle("seeded", (3, 7), (100003, 100007, 100009, 100019), 10**9)
        self.assertEqual(self.run_solver([puzzle], 2, 13), self.run_solver([puzzle], 2, 13))

    def polish(self, puzzle, hint, expect_success=True, mode=6):
        fields = [puzzle.id, puzzle.value_limit, len(puzzle.starting_values),
                  len(puzzle.targets), *puzzle.starting_values, *puzzle.targets, len(hint)]
        for instruction in hint:
            fields.extend(instruction)
        response = subprocess.run(
            [str(self.binary), "2", ".18", "32", "40", "18", str(mode), "0", "3", "1"],
            input=" ".join(map(str, fields)) + "\n", text=True, capture_output=True, timeout=30,
        )
        if not expect_success:
            self.assertNotEqual(response.returncode, 0)
            return
        self.assertEqual(response.returncode, 0, response.stderr)
        identifier, encoded = response.stdout.strip().split(" ", 1)
        self.assertEqual(identifier, puzzle.id)
        program = json.loads(encoded)
        validate_program(puzzle, program)
        self.assertLessEqual(len(program), len(hint))
        return program

    def test_polish_preserves_valid_hint_with_shared_descendants(self):
        puzzle = Puzzle("descendants", (3,), (5, 10, 15, 25), 25)
        hint = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3],
                ["+", 3, 3, 4], ["+", 4, 3, 5], ["+", 5, 4, 6]]
        self.polish(puzzle, hint)

    def test_polish_keeps_hint_if_protected_seed_allocator_cannot_fit(self):
        puzzle = Puzzle("full_bank", tuple(range(1, 49)), (49,), 100)
        hint = [["+", 0, 47, 0]]
        self.assertEqual(self.polish(puzzle, hint), hint)

    def test_polish_rejects_invalid_exact_division(self):
        self.polish(Puzzle("invalid_division", (3, 2), (1,), 10), [["/", 0, 1, 2]], False)

    def test_polish_rejects_missing_targets_and_large_hint_count(self):
        self.polish(Puzzle("missing", (3,), (5,), 10), [], False)
        response = subprocess.run(
            [str(self.binary), "2", ".18", "32", "40", "18", "6"],
            input="oversized 10 1 1 3 3 4097\n", text=True, capture_output=True, timeout=10,
        )
        self.assertNotEqual(response.returncode, 0)

    def test_internal_hub_repair_reduces_whole_shared_program(self):
        puzzle = Puzzle("internal_hub", (3, 5), (24, 36, 60), 100)
        hint = [["/", 0, 0, 2], ["-", 0, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 4, 6], ["+", 6, 6, 7],
                ["+", 7, 6, 8], ["+", 7, 8, 9]]
        repaired = self.polish(puzzle, hint, mode=7)
        self.assertLess(len(repaired), len(hint))

    def test_internal_repair_preserves_descendant_and_limit_rules(self):
        puzzle = Puzzle("hub_tight", (3,), (5, 10, 15, 25), 25)
        hint = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3],
                ["+", 3, 3, 4], ["+", 4, 3, 5], ["+", 5, 4, 6]]
        self.polish(puzzle, hint, mode=7)

    def test_exclusive_diamond_cone_can_be_rebuilt(self):
        # The 12 is used twice inside a wholly exclusive diamond. A fanout-one
        # cut cannot remove it, while the outside-DAG cut can reconstruct 36
        # directly from the two free starting values.
        puzzle = Puzzle("exclusive_diamond", (3, 5), (36, 60), 100)
        hint = [["/", 0, 0, 2], ["-", 0, 2, 3], ["+", 3, 3, 4],
                ["+", 4, 4, 5], ["+", 5, 4, 6], ["+", 6, 6, 7],
                ["+", 7, 6, 8], ["+", 8, 7, 9]]
        repaired = self.polish(puzzle, hint, mode=8)
        self.assertLess(len(repaired), len(hint))

    def test_outside_cut_retains_values_shared_by_other_targets(self):
        puzzle = Puzzle("outside_shared", (3,), (5, 10, 15, 25), 25)
        hint = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3],
                ["+", 3, 3, 4], ["+", 4, 3, 5], ["+", 5, 4, 6]]
        self.polish(puzzle, hint, mode=8)

    def test_joint_tail_rebuild_shares_new_target_constructions(self):
        puzzle = Puzzle("joint_tail", (10,), (100, 200, 300), 500)
        hint = [["+", 0, 0, 1], ["+", 1, 0, 2], ["+", 2, 0, 3],
                ["+", 3, 0, 4], ["+", 4, 4, 5], ["+", 5, 5, 6],
                ["+", 5, 6, 7]]
        repaired = self.polish(puzzle, hint, mode=9)
        self.assertEqual(len(repaired), 3)

    def test_joint_tail_keeps_protected_seed_fallback_and_tight_limits(self):
        puzzle = Puzzle("tail_full_bank", tuple(range(1, 49)), (49,), 100)
        hint = [["+", 0, 47, 0]]
        self.assertEqual(self.polish(puzzle, hint, mode=9), hint)
        tight = Puzzle("tail_tight", (3,), (5, 10, 15, 25), 25)
        program = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3],
                   ["+", 3, 3, 4], ["+", 4, 3, 5], ["+", 5, 4, 6]]
        self.polish(tight, program, mode=9)


if __name__ == "__main__":
    unittest.main()
