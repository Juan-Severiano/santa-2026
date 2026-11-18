#!/usr/bin/env python3
"""Parallel, resumable experiments that retain only validated improvements."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib
import importlib.metadata
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from santa2026.core import (  # noqa: E402
    read_puzzles, read_submission, score_submission, validate_program, write_submission,
)


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_puzzles(puzzles, costs, count):
    """Evenly sample the full distribution of baseline instruction counts."""
    ordered = sorted(puzzles, key=lambda puzzle: (costs[puzzle.id].instructions, puzzle.id))
    if not count or count >= len(ordered):
        return ordered
    if count < 0:
        raise ValueError("sample must be nonnegative")
    # Midpoints give exactly count unique, deterministic, evenly spaced ranks.
    return [ordered[((2 * index + 1) * len(ordered)) // (2 * count)] for index in range(count)]


def _initialize(snapshot, beam_binary=None, beam_mode=0):
    # Each experiment runs immutable source copies even while a new strategy
    # is being developed in the main checkout. core's dataclasses stay shared.
    import santa2026
    santa2026.__path__.insert(0, str(Path(snapshot) / "santa2026"))
    if beam_binary:
        os.environ["SANTA2026_BEAM_BINARY"] = beam_binary
    os.environ["SANTA2026_BEAM_MODE"] = str(beam_mode)


def _evaluate(task):
    puzzle, original, strategies, seeds, effort, signature = task
    best = [instruction[:] for instruction in original]
    attempts = []
    for strategy in strategies:
        module_name, function_name = {
            "shared": ("shared", "solve_shared"),
            "synthesis": ("synthesis", "solve_synthesis"),
            "optimize": ("optimizer", "optimize_program"),
            "factor": ("factor", "solve_factor"),
            "beam": ("beam", "solve_beam"),
            "milp": ("milp", "optimize_milp"),
            "polish": ("beam", "polish_beam"),
        }[strategy]
        function = getattr(importlib.import_module("santa2026." + module_name), function_name)
        for seed in seeds:
            started = time.monotonic()
            attempt = {"strategy": strategy, "seed": seed}
            try:
                if strategy in ("optimize", "milp", "polish"):
                    candidate = function(puzzle, [instruction[:] for instruction in best], seed=seed, effort=effort)
                else:
                    candidate = function(puzzle, seed=seed, effort=effort)
                result = validate_program(puzzle, candidate)
                attempt["instructions"] = result.instructions
                attempt["saved"] = max(0, len(best) - result.instructions)
                if result.instructions < len(best):
                    best = candidate
            except Exception as exc:
                attempt["error"] = f"{type(exc).__name__}: {exc}"
            attempt["seconds"] = round(time.monotonic() - started, 6)
            attempts.append(attempt)
    return {
        "id": puzzle.id, "signature": signature, "program": best,
        "baseline_instructions": len(original), "instructions": len(best), "attempts": attempts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puzzles", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategies", nargs="+", choices=("shared", "synthesis", "optimize", "factor", "beam", "milp", "polish"), required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0])
    parser.add_argument("--effort", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 8))
    parser.add_argument("--sample", type=int, default=100, help="0 evaluates all puzzles")
    parser.add_argument("--beam-binary", type=Path)
    parser.add_argument("--beam-mode", type=int, choices=(0, 1, 2, 3, 4), default=0)
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.effort < 1:
        parser.error("jobs and effort must be positive")
    started = time.monotonic()
    puzzles = read_puzzles(args.puzzles)
    baseline = read_submission(args.baseline)
    costs = score_submission(puzzles, baseline)
    selected = select_puzzles(puzzles, costs, args.sample)
    if not selected:
        raise ValueError("no puzzles selected")
    args.output.mkdir(parents=True, exist_ok=True)
    source = REPOSITORY / "src"
    hashes = {str(path.relative_to(source)): _hash(path) for path in sorted((source / "santa2026").rglob("*.py"))}
    code_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    snapshot = args.output / "code" / code_id
    if not snapshot.exists():
        shutil.copytree(source / "santa2026", snapshot / "santa2026", ignore=shutil.ignore_patterns("__pycache__"))
    snapshot_hashes = {str(path.relative_to(snapshot)): _hash(path) for path in sorted(snapshot.rglob("*.py"))}
    if snapshot_hashes != hashes:
        raise ValueError("source changed while snapshotting; rerun after the edit finishes")
    configuration = {
        "puzzles_sha256": _hash(args.puzzles), "baseline_sha256": _hash(args.baseline),
        "strategies": args.strategies, "seeds": args.seeds, "effort": args.effort,
        "source_hashes": hashes, "selected_ids": [puzzle.id for puzzle in selected],
        "python_version": sys.version.split()[0],
    }
    configuration["dependency_versions"] = {}
    for dependency in ("numpy", "scipy"):
        try:
            configuration["dependency_versions"][dependency] = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            configuration["dependency_versions"][dependency] = None
    if any(strategy in args.strategies for strategy in ("beam", "polish")):
        from santa2026.beam import binary_path
        executable = args.beam_binary.resolve() if args.beam_binary else binary_path()
        binary_hash = _hash(executable)
        configuration["beam_binary_sha256"] = binary_hash
        frozen_executable = args.output / "binaries" / binary_hash
        frozen_executable.parent.mkdir(exist_ok=True)
        if not frozen_executable.exists():
            shutil.copy2(executable, frozen_executable)
        if _hash(frozen_executable) != binary_hash:
            raise ValueError("binary changed while snapshotting; retry after compilation")
        executable = frozen_executable.resolve()
        configuration["beam_mode"] = args.beam_mode
        if "polish" in args.strategies:
            configuration["polish_mode"] = 6
    signature = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    _atomic_json(args.output / "manifest.json", dict(configuration, signature=signature))
    checkpoints = args.output / "checkpoints"
    best_programs = dict(baseline)
    records = {}
    pending = []
    for puzzle in selected:
        path = checkpoints / f"{puzzle.id}.json"
        record = None
        if path.exists():
            loaded = json.loads(path.read_text())
            if loaded.get("signature") == signature:
                validate_program(puzzle, loaded["program"])
                if len(loaded["program"]) <= len(baseline[puzzle.id]):
                    record = loaded
        if record is None:
            pending.append((puzzle, baseline[puzzle.id], args.strategies, args.seeds, args.effort, signature))
        else:
            records[puzzle.id] = record
            best_programs[puzzle.id] = record["program"]
    original_score = sum(item.instructions for item in costs.values())
    print(f"Selected {len(selected)}/{len(puzzles)} puzzles; resumed {len(records)}; workers={args.jobs}", flush=True)
    beam_binary = str(executable) if any(strategy in args.strategies for strategy in ("beam", "polish")) else None
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, initializer=_initialize,
            initargs=(str(snapshot.resolve()), beam_binary, args.beam_mode)) as pool:
        futures = [pool.submit(_evaluate, task) for task in pending]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            records[record["id"]] = record
            best_programs[record["id"]] = record["program"]
            _atomic_json(checkpoints / f"{record['id']}.json", record)
            if len(records) % 10 == 0 or len(records) == len(selected):
                saved = sum(r["baseline_instructions"] - r["instructions"] for r in records.values())
                print(f"Completed {len(records)}/{len(selected)}; saved={saved}; full_portfolio={original_score-saved}", flush=True)
    output_csv = args.output / "best.csv"
    write_submission(output_csv, best_programs)
    verified = score_submission(puzzles, read_submission(output_csv))
    final_score = sum(item.instructions for item in verified.values())
    report = {
        "signature": signature, "puzzles": len(puzzles), "evaluated": len(selected),
        "baseline_score": original_score, "score": final_score,
        "instructions_saved": original_score - final_score,
        "selected_baseline_score": sum(len(baseline[p.id]) for p in selected),
        "selected_score": sum(len(best_programs[p.id]) for p in selected),
        "puzzles_improved": sum(len(best_programs[p.id]) < len(baseline[p.id]) for p in selected),
        "errors": [{"id": r["id"], **a} for r in records.values() for a in r["attempts"] if "error" in a],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "submission_sha256": _hash(output_csv),
        "per_puzzle": {puzzle.id: {
            "baseline": len(baseline[puzzle.id]), "best": len(best_programs[puzzle.id]),
            "attempts": records[puzzle.id]["attempts"],
        } for puzzle in selected},
    }
    _atomic_json(args.output / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("per_puzzle", "errors")}, indent=2), flush=True)
    if report["errors"]:
        print(f"Strategy failures: {len(report['errors'])}; see report.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
