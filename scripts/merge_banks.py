#!/usr/bin/env python3
"""Scan CSV files for (id, program) rows, replay each program exactly, keep the shortest per puzzle.

Every candidate is replayed with ``santa2026.core.validate_program``; invalid rows are counted and
ignored. Nothing is executed from the scanned directories: files are read as data only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from santa2026.core import read_puzzles, read_submission, score_submission, validate_program, write_submission

csv.field_size_limit(1 << 28)


def candidate_rows(path: Path):
    """Yield (id, program cell) pairs from a CSV that has ``id`` and ``program`` columns."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            names = reader.fieldnames or []
            if "id" not in names or "program" not in names:
                return
            for row in reader:
                yield row["id"], row["program"] or ""
    except (OSError, UnicodeDecodeError, csv.Error):
        return


def merge_banks(puzzles, start, roots):
    """Return (best programs, per-file statistics, {puzzle id: file that supplied the shorter program})."""
    by_id = {puzzle.id: puzzle for puzzle in puzzles}
    best = dict(start)
    source: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    per_file = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.csv"))
        for path in files:
            total = ok = improved = bad = 0
            for pid, cell in candidate_rows(path):
                if pid not in by_id:
                    continue
                total += 1
                key = (pid, hashlib.sha256(cell.encode()).hexdigest())
                if key in seen:
                    continue
                seen.add(key)
                try:
                    program = json.loads(cell)
                    validate_program(by_id[pid], program)
                except (ValueError, TypeError, RecursionError):
                    bad += 1
                    continue
                ok += 1
                if len(program) < len(best[pid]):
                    best[pid] = program
                    source[pid] = str(path)
                    improved += 1
            if total:
                per_file.append({"file": str(path), "rows": total, "valid": ok, "invalid": bad, "improved": improved})
    return best, per_file, source


def total_instructions(programs) -> int:
    return sum(len(program) for program in programs.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puzzles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=Path, required=True, help="complete validated submission to start from")
    parser.add_argument("--report", type=Path)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args(argv)
    puzzles = read_puzzles(args.puzzles)
    start = read_submission(args.start)
    score_submission(puzzles, start)
    best, per_file, source = merge_banks(puzzles, start, args.roots)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_submission(args.output, best)
    verified = score_submission(puzzles, read_submission(args.output))
    report = {"start_score": total_instructions(start),
              "score": sum(result.instructions for result in verified.values()),
              "submission_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
              "files": per_file, "improved_by": source}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("start_score", "score", "submission_sha256")}, indent=2))
    for item in per_file:
        if item["improved"]:
            print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
