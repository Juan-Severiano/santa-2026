#!/usr/bin/env python3
"""Merge complete validated submissions, keeping the shortest program per puzzle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from santa2026.core import read_puzzles, read_submission, score_submission, write_submission


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puzzles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("submissions", nargs="+", type=Path)
    args = parser.parse_args(argv)
    puzzles = read_puzzles(args.puzzles)
    best = None
    inputs = []
    for path in args.submissions:
        programs = read_submission(path)
        results = score_submission(puzzles, programs)
        inputs.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "score": sum(r.instructions for r in results.values())})
        if best is None:
            best = programs
        else:
            for puzzle in puzzles:
                if len(programs[puzzle.id]) < len(best[puzzle.id]):
                    best[puzzle.id] = programs[puzzle.id]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_submission(args.output, best)
    verified = score_submission(puzzles, read_submission(args.output))
    report = {"inputs": inputs, "puzzles": len(puzzles), "score": sum(r.instructions for r in verified.values()),
              "submission_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
