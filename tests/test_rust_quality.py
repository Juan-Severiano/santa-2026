import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from santa2026.core import Puzzle


SPEC = importlib.util.spec_from_file_location("rust_quality", Path(__file__).resolve().parents[1] / "scripts/rust_quality.py")
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


class RustQualityTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = Puzzle("toy", (3,), (5,), 10)
        self.original = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 1, 1, 3], ["+", 0, 2, 4]]

    def test_invalid_output_and_timeout_preserve_input(self):
        invalid = subprocess.CompletedProcess([], 0, "puzzle []\n", "")
        with patch.object(quality.subprocess, "run", side_effect=[invalid, subprocess.TimeoutExpired("engine", 1)]):
            record = quality.search("repair", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], self.original)
        self.assertIsNot(record["program"], self.original)
        self.assertIn("error", record["attempts"][0])
        self.assertTrue(record["attempts"][1]["budget_exhausted"])

    def test_valid_smaller_program_survives_later_timeout(self):
        shorter = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3]]
        valid = subprocess.CompletedProcess([], 0, "puzzle " + json.dumps(shorter) + "\n", "")
        with patch.object(quality.subprocess, "run", side_effect=[valid, subprocess.TimeoutExpired("engine", 1)]):
            record = quality.search("tail", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], shorter)
        self.assertEqual(len(self.original), 4)

    def test_complete_valid_timeout_output_is_recovered(self):
        shorter = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3]]
        interrupted = subprocess.TimeoutExpired("engine", 1,
            output=("puzzle " + json.dumps(shorter) + "\n").encode())
        with patch.object(quality.subprocess, "run", side_effect=interrupted):
            record = quality.search("tail", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], shorter)
        self.assertTrue(record["attempts"][0]["recovered_complete_output"])

    def test_constructor_keeps_longer_diverse_candidate_without_regressing_best(self):
        longer = self.original + [["+", 1, 1, 5]]
        valid = subprocess.CompletedProcess([], 0, "puzzle " + json.dumps(longer) + "\n", "")
        with patch.object(quality.subprocess, "run", side_effect=[valid, subprocess.TimeoutExpired("engine", 1)]):
            record = quality.search("construct", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], self.original)
        self.assertEqual(record["generated_programs"], [longer])

    def test_joint_payload_counts_programs_before_instructions(self):
        data = quality.payload(self.puzzle, [self.original, self.original], joint=True).split()
        self.assertEqual(data[:8], ["puzzle", "10", "1", "1", "3", "5", "2", "4"])

    def test_lns_uses_one_continuous_trajectory_and_preserves_best(self):
        longer = self.original + [["+", 1, 1, 5]]
        valid = subprocess.CompletedProcess([], 0, "puzzle " + json.dumps(longer) + "\n", "")
        with patch.object(quality.subprocess, "run", return_value=valid) as run:
            record = quality.search("lns", Path("engine"), self.puzzle, self.original, [], 15,
                                    lns_flags=7, lns_seed=42)
        self.assertEqual(record["program"], self.original)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[1], "42")
        self.assertGreater(int(command[2]), 13000)
        self.assertLess(int(command[2]), 15000)
        self.assertEqual(command[3], "7")

    def test_lns_timeout_keeps_valid_input(self):
        with patch.object(quality.subprocess, "run", side_effect=subprocess.TimeoutExpired("engine", 1)):
            record = quality.search("lns", Path("engine"), self.puzzle, self.original, [], 1)
        self.assertEqual(record["program"], self.original)
        self.assertEqual(len(record["attempts"]), 1)
        self.assertTrue(record["attempts"][0]["budget_exhausted"])

    def test_lns_archive_validates_deduplicates_and_preserves_shortest(self):
        shorter = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3]]
        longer = self.original + [["+", 1, 1, 5]]
        archive_paths = []

        def run(command, **kwargs):
            archive_path = Path(kwargs["env"]["SANTA2026_LNS_ARCHIVE"])
            archive_paths.append(archive_path)
            quality.atomic_json(archive_path, {"version": 1, "id": "puzzle",
                "programs": [longer, longer, shorter]})
            return subprocess.CompletedProcess(command, 0, "puzzle " + json.dumps(self.original) + "\n", "")

        with patch.object(quality.subprocess, "run", side_effect=run):
            record = quality.search("lns", Path("engine"), self.puzzle, self.original, [], 1,
                                    lns_archive=True)
        self.assertEqual(record["generated_programs"], [longer, shorter])
        self.assertEqual(record["program"], shorter)
        self.assertEqual(record["attempts"][0]["archive_instructions"], [5, 3])
        self.assertFalse(archive_paths[0].exists())

    def test_longer_lns_archive_does_not_promote_best(self):
        longer = self.original + [["+", 1, 1, 5]]

        def run(command, **kwargs):
            quality.atomic_json(Path(kwargs["env"]["SANTA2026_LNS_ARCHIVE"]),
                {"version": 1, "id": "puzzle", "programs": [longer]})
            return subprocess.CompletedProcess(command, 0, "puzzle " + json.dumps(self.original) + "\n", "")

        with patch.object(quality.subprocess, "run", side_effect=run):
            record = quality.search("lns", Path("engine"), self.puzzle, self.original, [], 1,
                                    lns_archive=True)
        self.assertEqual(record["program"], self.original)
        self.assertEqual(record["generated_programs"], [longer])

    def test_complete_lns_archive_survives_child_timeout(self):
        shorter = [["/", 0, 0, 1], ["-", 0, 1, 2], ["+", 0, 2, 3]]

        def run(command, **kwargs):
            quality.atomic_json(Path(kwargs["env"]["SANTA2026_LNS_ARCHIVE"]),
                {"version": 1, "id": "puzzle", "programs": [shorter]})
            raise subprocess.TimeoutExpired(command, 1)

        with patch.object(quality.subprocess, "run", side_effect=run):
            record = quality.search("lns", Path("engine"), self.puzzle, self.original, [], 1,
                                    lns_archive=True)
        self.assertEqual(record["program"], shorter)
        self.assertEqual(record["generated_programs"], [shorter])
        self.assertTrue(record["attempts"][0]["budget_exhausted"])

    def test_malformed_lns_archive_is_ignored_and_reported(self):
        for sidecar in [
            '{"version":1,',
            json.dumps({"version": True, "id": "puzzle", "programs": []}),
            json.dumps({"version": 1, "id": "wrong", "programs": []}),
            json.dumps({"version": 1, "id": "puzzle", "programs": [self.original] * 9}),
            json.dumps({"version": 1, "id": "puzzle", "programs": [self.original, []]}),
        ]:
            with self.subTest(sidecar=sidecar):
                def run(command, **kwargs):
                    Path(kwargs["env"]["SANTA2026_LNS_ARCHIVE"]).write_text(sidecar)
                    return subprocess.CompletedProcess(command, 0,
                        "puzzle " + json.dumps(self.original) + "\n", "")

                with patch.object(quality.subprocess, "run", side_effect=run):
                    record = quality.search("lns", Path("engine"), self.puzzle, self.original, [], 1,
                                            lns_archive=True)
                self.assertEqual(record["program"], self.original)
                self.assertEqual(record["generated_programs"], [])
                self.assertIn("archive_error", record["attempts"][0])

    def test_lns_raw_portfolios_keep_longer_archives_and_baseline_elsewhere(self):
        longer = self.original + [["+", 1, 1, 5]]
        longest = longer + [["+", 1, 1, 6]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            puzzles_path = root / "puzzles.csv"
            with puzzles_path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["id", "starting_values", "targets", "value_limit", "registers"])
                for identifier in ["toy", "unselected"]:
                    writer.writerow([identifier, "[3]", "[5]", 10, 48])
            baseline_path = root / "baseline.csv"
            quality.write_submission(baseline_path, {"toy": self.original, "unselected": self.original})
            ids_path = root / "ids.json"
            ids_path.write_text('["toy"]')
            binary = root / "engine"
            binary.write_text("test engine")
            output = root / "output"
            row = {"id": "toy", "engine": "lns", "baseline_instructions": 4, "instructions": 4,
                "program": self.original, "generated_programs": [longer, longest], "attempts": [],
                "wall_seconds": 0, "cpu_seconds": 0}
            argv = ["rust_quality.py", "--puzzles", str(puzzles_path), "--baseline", str(baseline_path),
                "--ids-file", str(ids_path), "--methods", "lns", "--lns", str(binary), "--lns-archive",
                "--output", str(output)]
            with patch.object(sys, "argv", argv), patch.object(quality, "search", return_value=row), \
                    patch("builtins.print"):
                quality.main()
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["score"], 8)
            self.assertEqual(report["engines"]["lns"]["saved"], 0)
            self.assertEqual(report["engines"]["lns"]["archived_programs"], 2)
            self.assertEqual([raw["score"] for raw in report["engines"]["lns"]["raw_portfolios"]], [9, 10])
            self.assertEqual(quality.read_submission(output / "lns-generated-0.csv")["toy"], longer)
            self.assertEqual(quality.read_submission(output / "lns-generated-1.csv")["unselected"], self.original)
            self.assertTrue(json.loads((output / "manifest.json").read_text())["lns_archive"])


if __name__ == "__main__":
    unittest.main()
