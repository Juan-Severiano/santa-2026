"""Local commands. These commands never submit to Kaggle."""

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

from .core import read_puzzles, read_submission, score_submission, write_submission
from .solver import solve


def _summary(results):
    lengths = [item.instructions for item in results.values()]
    return {
        "puzzles": len(lengths),
        "score": sum(lengths),
        "mean_instructions": statistics.mean(lengths) if lengths else 0,
        "max_instructions": max(lengths, default=0),
        "per_puzzle": {key: value.instructions for key, value in results.items()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Describe an official puzzle CSV")
    inspect.add_argument("puzzles", type=Path)
    validate = commands.add_parser("validate", help="Replay every instruction using exact integers")
    validate.add_argument("puzzles", type=Path)
    validate.add_argument("submission", type=Path)
    validate.add_argument("--report", type=Path)
    generate = commands.add_parser("solve", help="Generate and verify a local submission")
    generate.add_argument("puzzles", type=Path)
    generate.add_argument("output", type=Path)
    generate.add_argument("--fallback", type=Path, help="Keep a supplied program when it is shorter")
    generate.add_argument("--strategy", choices=("radix", "window", "portfolio"), default="portfolio")
    generate.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    try:
        started = time.monotonic()
        puzzles = read_puzzles(args.puzzles)
        if args.command == "inspect":
            counts = [len(set(p.targets) - set(p.starting_values)) for p in puzzles]
            print(json.dumps({
                "puzzles": len(puzzles),
                "starting_values_range": [min(map(lambda p: len(p.starting_values), puzzles)),
                                          max(map(lambda p: len(p.starting_values), puzzles))],
                "targets_range": [min(map(lambda p: len(p.targets), puzzles)),
                                  max(map(lambda p: len(p.targets), puzzles))],
                "max_value_limit": max(p.value_limit for p in puzzles),
                "target_count_lower_bound": sum(counts),
            }, indent=2))
            return 0

        if args.command == "validate":
            results = score_submission(puzzles, read_submission(args.submission))
            report = _summary(results)
        else:
            fallback = read_submission(args.fallback) if args.fallback else None
            fallback_results = score_submission(puzzles, fallback) if fallback is not None else None
            programs = {}
            failures = {}
            for index, puzzle in enumerate(puzzles, 1):
                try:
                    program = solve(puzzle, strategy=args.strategy)
                except ValueError as exc:
                    if fallback is None:
                        raise
                    failures[puzzle.id] = str(exc)
                    program = fallback[puzzle.id]
                if fallback is not None and len(fallback[puzzle.id]) < len(program):
                    program = fallback[puzzle.id]
                programs[puzzle.id] = program
                if index % 100 == 0:
                    print(f"Solved {index}/{len(puzzles)} puzzles", file=sys.stderr, flush=True)
            score_submission(puzzles, programs)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_submission(args.output, programs)
            # Check the exact artifact after the JSON/CSV serialization round trip.
            results = score_submission(puzzles, read_submission(args.output))
            report = _summary(results)
            report["strategy"] = args.strategy
            report["solver_failures"] = failures
            if fallback_results is not None:
                report["fallback_score"] = sum(r.instructions for r in fallback_results.values())
                report["instructions_saved"] = report["fallback_score"] - report["score"]
                report["puzzles_improved"] = sum(
                    results[key].instructions < fallback_results[key].instructions for key in results
                )
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["python_version"] = platform.python_version()
        report["puzzles_sha256"] = hashlib.sha256(args.puzzles.read_bytes()).hexdigest()
        artifact = args.submission if args.command == "validate" else args.output
        report["submission_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if args.command == "solve":
            report["solver_source_sha256"] = hashlib.sha256(
                Path(__file__).with_name("solver.py").read_bytes()
            ).hexdigest()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "per_puzzle"}, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
