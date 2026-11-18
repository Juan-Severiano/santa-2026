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

    def polish(self, puzzle, hint, expect_success=True):
        fields = [puzzle.id, puzzle.value_limit, len(puzzle.starting_values),
                  len(puzzle.targets), *puzzle.starting_values, *puzzle.targets, len(hint)]
        for instruction in hint:
            fields.extend(instruction)
        response = subprocess.run(
            [str(self.binary), "2", ".18", "32", "40", "18", "6", "0", "3", "1"],
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


if __name__ == "__main__":
    unittest.main()
