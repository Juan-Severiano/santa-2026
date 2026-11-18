import copy
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from santa2026.core import Puzzle, read_submission

_spec = importlib.util.spec_from_file_location("compare_languages", Path(__file__).resolve().parents[1] / "scripts" / "compare_languages.py")
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


class LanguageCompareTests(unittest.TestCase):
    def setUp(self):
        self.first = Puzzle("one", (3,), (1,), 3)
        self.second = Puzzle("another id with spaces", (4,), (1,), 4)
        self.baseline = {p.id: [["/", 0, 0, 1]] for p in (self.first, self.second)}
        self.payload, self.aliases = compare.encode_batch([self.first, self.second], self.baseline)

    def test_protocol_uses_stable_safe_identifiers_and_keeps_hints(self):
        self.assertEqual(list(self.aliases), ["p_00000", "p_00001"])
        self.assertNotIn(b"another id", self.payload)
        self.assertIn(b"\n1\n/ 0 0 1\n", self.payload)

    def test_invalid_missing_duplicate_and_extra_ids_are_flagged(self):
        text = 'p_00000 [["/",0,0,1]]\np_00000 [["/",0,0,1]]\nunknown []\np_00001 []\n'
        programs, report = compare.parse_output(text, self.aliases)
        self.assertFalse(report["valid"])
        self.assertEqual(report["duplicate_ids"], ["one"])
        self.assertEqual(report["extra_ids"], ["unknown"])
        self.assertEqual(report["missing_ids"], [self.second.id])
        self.assertEqual(len(programs), 1)

    def test_timeout_keeps_only_complete_valid_lines(self):
        text = 'p_00000 [["/",0,0,1]]\np_00001 [["/",0,'
        programs, report = compare.parse_output(text, self.aliases, require_complete=False, complete_lines_only=True)
        self.assertTrue(report["valid"])
        self.assertFalse(report["complete"])
        self.assertEqual(list(programs), [self.first.id])
        self.assertGreater(report["discarded_partial_bytes"], 0)

    def test_fallback_portfolio_and_baseline_are_preserved(self):
        original = copy.deepcopy(self.baseline)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "portfolio.csv"
            score = compare.merge_validated([self.first, self.second], self.baseline,
                                           {self.first.id: [["/", 0, 0, 1], ["+", 1, 1, 2]]}, output)
            self.assertEqual(score, 2)
            self.assertEqual(read_submission(output), original)
        self.assertEqual(self.baseline, original)

    def test_latin_square_balances_startup_position(self):
        names = ["cpp", "rust", "go"]
        orders = [compare.latin_square_order(names, repeat) for repeat in range(3)]
        for position in range(3):
            self.assertEqual({order[position] for order in orders}, set(names))

    def test_score_mismatch_cannot_be_called_language_speed(self):
        def run(length, digest):
            return {"all_outputs_valid": True, "batches": [{"program_lengths": {"one": length}, "program_sha256": {"one": digest}}]}
        fixed = {"cpp": [run(1, "same")] * 3, "go": [run(2, "different")] * 3}
        result = compare.fixed_differential(fixed, ["cpp", "go"], 3, 1)
        self.assertFalse(result["instruction_counts_match"])
        self.assertFalse(result["eligible_for_language_speed_claim"])

    def test_equal_length_different_programs_are_not_language_speed_parity(self):
        def run(digest):
            return {"all_outputs_valid": True, "batches": [{"program_lengths": {"one": 1}, "program_sha256": {"one": digest}}]}
        fixed = {"cpp": [run("first")] * 3, "go": [run("second")] * 3}
        result = compare.fixed_differential(fixed, ["cpp", "go"], 3, 1)
        self.assertTrue(result["instruction_counts_match"])
        self.assertFalse(result["identical_program_streams"])
        self.assertFalse(result["eligible_for_language_speed_claim"])

    def test_subprocess_executes_complete_protocol_and_preserves_baseline(self):
        original = copy.deepcopy(self.baseline)
        script = '''import json, sys
assert sys.argv[1:] == ["3", "0.18", "52", "56", "20", "6", "0", "12", "1"]
while True:
    header = sys.stdin.readline()
    if not header:
        break
    count = int(sys.stdin.readline())
    program = []
    for _ in range(count):
        op, i, j, d = sys.stdin.readline().split()
        program.append([op, int(i), int(j), int(d)])
    print(header.split()[0], json.dumps(program), flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory) / "engine"
            engine.write_text(f"#!{sys.executable}\n" + script)
            engine.chmod(0o755)
            config = {"width": 3, "alpha": .18, "n_done": 52, "n_recent": 56,
                      "index_bits": 20, "trials": 12, "passes": 1}
            programs, record = compare.run_batch(engine, self.payload, self.aliases,
                                                 Path(directory) / "complete", config, 0, 5)
            self.assertEqual(programs, self.baseline)
            self.assertTrue(record["validation"]["valid"])
            self.assertTrue(record["validation"]["complete"])
            self.assertFalse(record["timed_out"])
            self.assertGreater(record["wall_seconds"], 0)
            self.assertIsNotNone(record["cpu_seconds"])
            self.assertEqual(record["program_lengths"], {p.id: 1 for p in (self.first, self.second)})
        self.assertEqual(self.baseline, original)

    def test_timed_out_subprocess_retains_flushed_complete_program(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "output-ready"
            script = f'''import sys, time
from pathlib import Path
print('p_00000 [["/",0,0,1]]', flush=True)
sys.stdout.write('p_00001 [["/",0,')
sys.stdout.flush()
Path({str(ready)!r}).write_text('ready')
time.sleep(60)
'''
            engine = Path(directory) / "engine"
            engine.write_text(f"#!{sys.executable}\n" + script)
            engine.chmod(0o755)
            config = {"width": 3, "alpha": .18, "n_done": 52, "n_recent": 56,
                      "index_bits": 20, "trials": 12, "passes": 1}
            real_popen = compare.subprocess.Popen

            def launch_when_output_ready(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                startup_deadline = time.monotonic() + 10
                while not ready.exists():
                    if time.monotonic() >= startup_deadline or process.poll() is not None:
                        try:
                            compare.os.killpg(process.pid, compare.signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.communicate()
                        self.fail("mock engine did not flush its output before the startup deadline")
                    time.sleep(.01)
                return process

            # Start the short timeout only after the real child has flushed both
            # its complete result and partial next line, even on a loaded host.
            with mock.patch.object(compare.subprocess, "Popen", side_effect=launch_when_output_ready):
                programs, record = compare.run_batch(engine, self.payload, self.aliases,
                                                     Path(directory) / "timeout", config, 0, .1, allow_partial=True)
            self.assertTrue(record["timed_out"])
            self.assertTrue(record["validation"]["valid"])
            self.assertFalse(record["validation"]["complete"])
            self.assertEqual(programs, {self.first.id: self.baseline[self.first.id]})
            self.assertGreater(record["validation"]["discarded_partial_bytes"], 0)

    def test_wrong_binary_build_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory) / "engine"
            engine.write_text("#!/bin/sh\nexit 0\n")
            engine.chmod(0o755)
            engine.with_suffix(".json").write_text(json.dumps({"binary_sha256": "stale"}))
            with self.assertRaisesRegex(ValueError, "build metadata"):
                compare._freeze_engines([f"cpp:{engine}"], Path(directory) / "output", [])


if __name__ == "__main__":
    unittest.main()
