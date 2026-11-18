"""Differential checks for the independently implemented native repair engines.

Build with scripts/build_native.py first. These checks use exact integer replay
and compare outputs, including register allocation and randomized target order.
"""

import json
from pathlib import Path
import subprocess
import unittest

from santa2026.core import Puzzle, validate_program


ROOT = Path(__file__).resolve().parents[1]


class NativeRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engines = {name: ROOT / "outputs/bin" / ("repair-" + name)
                       for name in ("cpp", "rust", "go")}
        missing = [name for name, path in cls.engines.items() if not path.is_file()]
        if missing:
            raise unittest.SkipTest("Build native engines first: " + ", ".join(missing))

    def run_case(self, puzzle, hint, seed=0):
        fields = ["puzzle", puzzle.value_limit, len(puzzle.starting_values),
                  len(puzzle.targets), *puzzle.starting_values, *puzzle.targets, len(hint)]
        for instruction in hint:
            fields.extend(instruction)
        payload = " ".join(map(str, fields)) + "\n"
        programs = {}
        for name, binary in self.engines.items():
            response = subprocess.run(
                [str(binary), "2", ".18", "32", "40", "18", "6", str(seed), "3", "1"],
                input=payload, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(response.returncode, 0, (name, response.stderr))
            identifier, encoded = response.stdout.strip().split(" ", 1)
            self.assertEqual(identifier, "puzzle")
            programs[name] = json.loads(encoded)
            validate_program(puzzle, programs[name])
            self.assertLessEqual(len(programs[name]), len(hint))
        self.assertEqual(programs["cpp"], programs["rust"])
        self.assertEqual(programs["cpp"], programs["go"])

    def test_tight_limit_shared_descendants_and_seed_orders(self):
        puzzle = Puzzle("descendants", (3,), (5, 10, 15, 25), 25)
        hint = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3],
                ["+", 3, 3, 4], ["+", 4, 3, 5], ["+", 5, 4, 6]]
        for seed in (0, 1, 2, 3, 7):
            with self.subTest(seed=seed):
                self.run_case(puzzle, hint, seed)

    def test_full_register_bank_preserves_legal_overwrite(self):
        self.run_case(Puzzle("full", tuple(range(1, 49)), (49,), 100), [["+", 0, 47, 0]])

    def test_products_at_maximum_limit(self):
        puzzle = Puzzle("max", (10**9, 10**9 - 1), (10**18, 10**18 - 10**9, 1), 10**18)
        self.run_case(puzzle, [["*", 0, 0, 2], ["*", 0, 1, 3], ["-", 0, 1, 4]])

    def test_targets_already_covered(self):
        self.run_case(Puzzle("covered", (1, 3), (1, 3), 3), [])

    def test_invalid_hint_is_rejected(self):
        for name, binary in self.engines.items():
            with self.subTest(engine=name):
                response = subprocess.run(
                    [str(binary), "2", ".18", "32", "40", "18", "6", "0", "3", "1"],
                    input="puzzle 10 2 1 3 2 1 1 / 0 1 2\n", text=True,
                    capture_output=True, timeout=10,
                )
                self.assertNotEqual(response.returncode, 0)


if __name__ == "__main__":
    unittest.main()
