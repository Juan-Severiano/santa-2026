import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from santa2026.core import Puzzle, ValidationResult

_spec = importlib.util.spec_from_file_location("santa_lab", Path(__file__).resolve().parents[1] / "scripts" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


class LabTests(unittest.TestCase):
    def frozen_fixture(self, root):
        expected = {"puzzles_sha256": "data", "baseline_sha256": "baseline",
                    "strategies": ["shared"], "seeds": [0], "effort": 1, "selected_ids": ["toy"]}
        hashes = {"santa2026/example.py": hashlib.sha256(b"pass\n").hexdigest()}
        configuration = dict(expected, source_hashes=hashes)
        signature = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        code_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
        snapshot = root / "code" / code_id
        (snapshot / "santa2026").mkdir(parents=True)
        (snapshot / "santa2026/example.py").write_bytes(b"pass\n")
        (root / "manifest.json").write_text(json.dumps(dict(configuration, signature=signature)))
        return expected, snapshot

    def test_frozen_resume_reuses_exact_saved_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, snapshot = self.frozen_fixture(root)
            configuration, signature, loaded, executable = lab._load_frozen(root, expected)
            self.assertEqual(loaded, snapshot)
            self.assertIsNone(executable)
            self.assertEqual(configuration["strategies"], ["shared"])

    def test_frozen_resume_rejects_tampering_and_different_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, snapshot = self.frozen_fixture(root)
            with self.assertRaisesRegex(ValueError, "baseline_sha256"):
                lab._load_frozen(root, dict(expected, baseline_sha256="other input"))
            (snapshot / "santa2026/example.py").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "damaged"):
                lab._load_frozen(root, expected)

    def test_frozen_resume_rejects_missing_repair_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = self.frozen_fixture(root)
            configuration = json.loads((root / "manifest.json").read_text())
            configuration.pop("signature")
            configuration["repair_binary_sha256"] = "missing-repair"
            signature = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
            (root / "manifest.json").write_text(json.dumps(dict(configuration, signature=signature)))
            with self.assertRaisesRegex(ValueError, "repair binary"):
                lab._load_frozen(root, expected)

    def test_frozen_resume_rejects_changed_native_search_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = self.frozen_fixture(root)
            options = dict(lab.DEFAULT_NATIVE_OPTIONS, joint_helpers=24)
            with self.assertRaisesRegex(ValueError, "native_options"):
                lab._load_frozen(root, dict(expected, native_options=options))

    def test_sample_spans_cost_distribution_without_duplicates(self):
        puzzles = [Puzzle(str(i), (1,), (1,), 1) for i in range(1000)]
        costs = {p.id: ValidationResult(int(p.id), 1) for p in puzzles}
        sample = lab.select_puzzles(puzzles, costs, 100)
        self.assertEqual(len({p.id for p in sample}), 100)
        self.assertLess(int(sample[0].id), 10)
        self.assertGreater(int(sample[-1].id), 990)

    def test_invalid_candidate_cannot_replace_baseline(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        original = [["/", 0, 0, 1]]
        module = SimpleNamespace(solve_shared=lambda *a, **kw: [])
        with patch.object(lab.importlib, "import_module", return_value=module):
            record = lab._evaluate((puzzle, original, ["shared"], [0], 1, "test"))
        self.assertEqual(record["program"], original)
        self.assertIn("error", record["attempts"][0])

    def test_mutating_optimizer_cannot_corrupt_baseline(self):
        puzzle = Puzzle("toy", (3,), (1,), 3)
        original = [["/", 0, 0, 1]]

        def broken_optimizer(puzzle, program, **kwargs):
            program.clear()
            return program

        with patch.object(lab.importlib, "import_module", return_value=SimpleNamespace(optimize_program=broken_optimizer)):
            record = lab._evaluate((puzzle, original, ["optimize"], [0], 1, "test"))
        self.assertEqual(record["program"], original)
        self.assertEqual(len(original), 1)


if __name__ == "__main__":
    unittest.main()
