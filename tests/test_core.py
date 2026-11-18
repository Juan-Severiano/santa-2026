import csv
import json
from pathlib import Path
import tempfile
import unittest

from santa2026.core import (
    MAX_INSTRUCTIONS,
    MAX_PROGRAM_BYTES,
    Puzzle,
    read_puzzles,
    read_submission,
    score_submission,
    validate_program,
    write_submission,
)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.example = Puzzle("example", (3,), (1, 2, 5, 8), 9)
        self.program = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3], ["+", 0, 3, 4]]

    def test_official_example(self):
        result = validate_program(self.example, self.program)
        self.assertEqual((result.instructions, result.covered_targets), (4, 4))

    def test_coverage_survives_overwrite_and_counts_initial_values(self):
        puzzle = Puzzle("overwrite", (3,), (1, 2, 3), 9)
        result = validate_program(puzzle, [["/", 0, 0, 1], ["-", 0, 1, 0], ["+", 0, 1, 0]])
        self.assertEqual(result.covered_targets, 3)
        self.assertEqual(result.instructions, 3)

    def test_source_values_read_before_destination_overwritten(self):
        self.assertEqual(validate_program(Puzzle("double", (3,), (6,), 6), [["+", 0, 0, 0]]).instructions, 1)

    def test_every_instruction_is_checked_even_after_targets_covered(self):
        with self.assertRaisesRegex(ValueError, "undefined"):
            validate_program(Puzzle("tail", (3,), (3,), 9), [["+", 1, 1, 0]])

    def test_exact_big_integer_arithmetic(self):
        value = 999_999_999_999_999_999
        puzzle = Puzzle("large", (value, 3), (333_333_333_333_333_333,), 10**18)
        validate_program(puzzle, [["/", 0, 1, 0]])

    def test_rejects_illegal_calculator_operations(self):
        cases = [
            [["+", 0, 1, 2]],  # undefined
            [["-", 0, 0, 0]],  # zero
            [["/", 0, 0, 1], ["-", 1, 0, 2]],  # negative
            [["+", 0, 0, 1], ["*", 1, 1, 2]],  # overflow
            [["/", 0, 0, 1], ["+", 1, 1, 1], ["/", 0, 1, 2]],  # remainder
            [["+", True, 0, 0]],
            [["+", 0.0, 0, 0]],
            [["+", -1, 0, 0]],
            [["+", 48, 0, 0]],
            [["+", 0, 0, 48]],
            [["%", 0, 0, 0]],
            [[["+"], 0, 0, 0]],
            [["+", 0, 0]],
            [("+", 0, 0, 0)],
            {"program": []},
        ]
        for program in cases:
            with self.subTest(program=program), self.assertRaises(ValueError):
                validate_program(Puzzle("bad", (3,), (), 9), program)

    def test_instruction_limit(self):
        puzzle = Puzzle("limit", (1,), (1,), 1)
        validate_program(puzzle, [["/", 0, 0, 0]] * MAX_INSTRUCTIONS)
        with self.assertRaisesRegex(ValueError, "instructions"):
            validate_program(puzzle, [["/", 0, 0, 0]] * (MAX_INSTRUCTIONS + 1))

    def test_missing_targets(self):
        with self.assertRaisesRegex(ValueError, "misses 4"):
            validate_program(self.example, [])

    def test_invalid_puzzle_values(self):
        invalid = [
            dict(starting_values=()),
            dict(starting_values=(True,)),
            dict(starting_values=(0,)),
            dict(targets=(10,)),
            dict(value_limit=True),
            dict(value_limit=10**18 + 1),
            dict(registers=49),
            dict(registers=0),
            dict(registers=1, starting_values=(1, 2)),
        ]
        for changes in invalid:
            fields = dict(id="bad", starting_values=(3,), targets=(), value_limit=9)
            fields.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Puzzle(**fields)

    def test_csv_round_trip_and_score_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "submission.csv"
            write_submission(path, {"example": self.program})
            loaded = read_submission(path)
            self.assertEqual(loaded, {"example": self.program})
            self.assertEqual(score_submission([self.example], loaded)["example"].instructions, 4)
        for programs in ({}, {"example": self.program, "extra": []}):
            with self.assertRaisesRegex(ValueError, "ID mismatch"):
                score_submission([self.example], programs)
        with self.assertRaisesRegex(ValueError, "duplicate puzzle"):
            score_submission([self.example, self.example], {"example": self.program})

    def test_strict_submission_csv_and_json(self):
        cases = [
            "program,id\n[],x\n",
            "id,program,extra\nx,[],foo\n",
            "id,program\nx,[]\nx,[]\n",
            "id,program\nx,[],foo\n",
            "id,program\n,[]\n",
            "id,program\nx,NaN\n",
            "id,program\nx,{}\n",
            "id,program\nx,[true]\n",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            for text in cases:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    read_submission(path)

    def test_raw_program_cell_byte_limit_counts_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "padded.csv"
            with path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["id", "program"])
                writer.writerow(["x", " " * MAX_PROGRAM_BYTES + "[]"])
            with self.assertRaises(ValueError):
                read_submission(path)

    def test_read_practice_csv_and_reject_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "practice.csv"
            with path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["id", "starting_values", "targets", "value_limit", "registers", "family"])
                writer.writerow(["example", "[3]", "[1,2,5,8]", "9", "48", "tiny"])
            self.assertEqual(read_puzzles(path), [self.example])
            with path.open("a", newline="") as stream:
                csv.writer(stream).writerow(["example", "[3]", "[3]", "9", "48", "tiny"])
            with self.assertRaisesRegex(ValueError, "duplicate puzzle"):
                read_puzzles(path)


if __name__ == "__main__":
    unittest.main()
