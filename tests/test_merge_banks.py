import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_lineages import collect_lineages
from merge_banks import merge_banks
from santa2026.core import Puzzle

PUZZLE = Puzzle("example", (3,), (1, 2, 5, 8), 9)
SHORT = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3], ["+", 0, 3, 4]]
LONG = SHORT + [["+", 1, 1, 5]]
CHEATING = SHORT[:3]  # shorter than SHORT, but never produces 8


def bank(path, rows, header="id,program"):
    lines = [header]
    for identifier, program in rows:
        cell = json.dumps(program, separators=(",", ":")).replace('"', '""')
        lines.append(f'{identifier},"{cell}"')
    path.write_text("\n".join(lines) + "\n")


class MergeBanksTests(unittest.TestCase):
    def test_keeps_shortest_valid_program_and_records_its_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bank(root / "a-invalid.csv", [("example", CHEATING), ("unknown", SHORT)])
            bank(root / "b-short.csv", [("example", SHORT)])
            bank(root / "c-other-columns.csv", [("example", CHEATING)], header="id,steps")
            best, per_file, source = merge_banks([PUZZLE], {"example": LONG}, [root])
        self.assertEqual(best["example"], SHORT)
        self.assertEqual(Path(source["example"]).name, "b-short.csv")
        counts = {Path(item["file"]).name: (item["valid"], item["invalid"], item["improved"]) for item in per_file}
        self.assertEqual(counts, {"a-invalid.csv": (0, 1, 0), "b-short.csv": (1, 0, 1)})

    def test_never_replaces_with_a_program_of_equal_or_greater_length(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bank(root / "long.csv", [("example", LONG)])
            best, _, source = merge_banks([PUZZLE], {"example": SHORT}, [root])
        self.assertEqual(best["example"], SHORT)
        self.assertEqual(source, {})


    def test_lineages_are_valid_alternatives_within_the_excess_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            far = SHORT + [["+", 1, 1, 5]] * 3
            bank(root / "bank.csv", [("example", SHORT), ("example", LONG), ("example", far), ("example", CHEATING)])
            lineages = collect_lineages([PUZZLE], {"example": SHORT}, [root], max_excess=1)
        self.assertEqual([(item["p"], item["len"], item["best"]) for item in lineages], [(LONG, 5, 4)])


if __name__ == "__main__":
    unittest.main()
