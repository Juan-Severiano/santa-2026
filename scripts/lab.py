#!/usr/bin/env python3
"""Parallel, resumable experiments that retain only validated improvements."""

from __future__ import annotations

import argparse
import concurrent.futures
from concurrent.futures.process import BrokenProcessPool
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
WORKER_CANDIDATES = []
DEFAULT_NATIVE_OPTIONS = {"optimizer_ms": None, "optimizer_boundary": 0, "joint_helpers": 0, "groups_direct": 0, "joint_plans": 0, "joint_ms": None}
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


def _load_frozen(output, requested):
    configuration = json.loads((output / "manifest.json").read_text())
    signature = configuration.pop("signature")
    if hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest() != signature:
        raise ValueError("the saved experiment manifest is damaged")
    for key in ("puzzles_sha256", "baseline_sha256", "strategies", "seeds", "effort", "selected_ids"):
        if configuration.get(key) != requested[key]:
            raise ValueError(f"frozen experiment does not match requested {key}")
    for key in ("python_version", "dependency_versions", "beam_mode"):
        if key in configuration and configuration[key] != requested.get(key):
            raise ValueError(f"frozen experiment requires its original {key}")
    if configuration.get("candidate_sha256", []) != requested.get("candidate_sha256", []):
        raise ValueError("frozen experiment requires its original candidate programs")
    for key in ("polish_mode", "repair_trials", "repair_passes"):
        if key in configuration and configuration[key] != requested.get(key):
            raise ValueError(f"frozen experiment requires its original {key}")
    if "native_options" in requested:
        saved_native = dict(DEFAULT_NATIVE_OPTIONS, **configuration.get("native_options", {}))
        if saved_native != dict(DEFAULT_NATIVE_OPTIONS, **requested["native_options"]):
            raise ValueError("frozen experiment requires its original native_options")
        if "rustjoint" in configuration["strategies"] and "native_options" not in configuration:
            raise ValueError("frozen joint experiment is missing signed native_options")
    hashes = configuration["source_hashes"]
    code_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    snapshot = output / "code" / code_id
    actual = {str(path.relative_to(snapshot)): _hash(path) for path in sorted(snapshot.rglob("*.py"))}
    if actual != hashes:
        raise ValueError("frozen Python source is missing or damaged")
    executable = None
    if "beam_binary_sha256" in configuration:
        executable = output / "binaries" / configuration["beam_binary_sha256"]
        if not executable.is_file() or _hash(executable) != configuration["beam_binary_sha256"]:
            raise ValueError("frozen C++ binary is missing or damaged")
        executable = executable.resolve()
    if "mitm_binary_sha256" in configuration:
        mitm_executable = output / "binaries" / configuration["mitm_binary_sha256"]
        if not mitm_executable.is_file() or _hash(mitm_executable) != configuration["mitm_binary_sha256"]:
            raise ValueError("frozen exact-search binary is missing or damaged")
    if "repair_binary_sha256" in configuration:
        repair_executable = output / "binaries" / configuration["repair_binary_sha256"]
        if not repair_executable.is_file() or _hash(repair_executable) != configuration["repair_binary_sha256"]:
            raise ValueError("frozen repair binary is missing or damaged")
    if "joint_binary_sha256" in configuration:
        joint_executable = output / "binaries" / configuration["joint_binary_sha256"]
        if not joint_executable.is_file() or _hash(joint_executable) != configuration["joint_binary_sha256"]:
            raise ValueError("frozen joint-recipe binary is missing or damaged")
    return configuration, signature, snapshot, executable


def select_puzzles(puzzles, costs, count):
    """Evenly sample the full distribution of baseline instruction counts."""
    ordered = sorted(puzzles, key=lambda puzzle: (costs[puzzle.id].instructions, puzzle.id))
    if not count or count >= len(ordered):
        return ordered
    if count < 0:
        raise ValueError("sample must be nonnegative")
    # Midpoints give exactly count unique, deterministic, evenly spaced ranks.
    return [ordered[((2 * index + 1) * len(ordered)) // (2 * count)] for index in range(count)]


def _initialize(snapshot, beam_binary=None, beam_mode=0, repair_mode=6, repair_trials=12, repair_passes=1, candidates=(), mitm_binary=None, repair_binary=None, joint_binary=None, native_options=None):
    # Each experiment runs immutable source copies even while a new strategy
    # is being developed in the main checkout. core's dataclasses stay shared.
    import santa2026
    santa2026.__path__.insert(0, str(Path(snapshot) / "santa2026"))
    if beam_binary:
        os.environ["SANTA2026_BEAM_BINARY"] = beam_binary
    else:
        os.environ.pop("SANTA2026_BEAM_BINARY", None)
    if repair_binary:
        os.environ["SANTA2026_REPAIR_BINARY"] = repair_binary
    else:
        os.environ.pop("SANTA2026_REPAIR_BINARY", None)
    os.environ["SANTA2026_BEAM_MODE"] = str(beam_mode)
    os.environ["SANTA2026_REPAIR_MODE"] = str(repair_mode)
    os.environ["SANTA2026_REPAIR_TRIALS"] = str(repair_trials)
    os.environ["SANTA2026_REPAIR_PASSES"] = str(repair_passes)
    native_options = native_options or DEFAULT_NATIVE_OPTIONS
    # Group synthesis shares the signed optimizer deadline. A separate shell
    # override must not silently change a resumed experiment.
    os.environ.pop("SANTA2026_GROUPS_MS", None)
    os.environ["SANTA2026_OPTIMIZER_BOUNDARY"] = str(native_options["optimizer_boundary"])
    os.environ["SANTA2026_JOINT_HELPERS"] = str(native_options["joint_helpers"])
    os.environ["SANTA2026_GROUPS_DIRECT"] = str(native_options.get("groups_direct", 0))
    os.environ["SANTA2026_JOINT_PLANS"] = str(native_options.get("joint_plans", 0))
    if native_options.get("joint_ms") is None:
        os.environ.pop("SANTA2026_JOINT_MS", None)
    else:
        os.environ["SANTA2026_JOINT_MS"] = str(native_options["joint_ms"])
    if native_options["optimizer_ms"] is None:
        os.environ.pop("SANTA2026_OPTIMIZER_MS", None)
    else:
        os.environ["SANTA2026_OPTIMIZER_MS"] = str(native_options["optimizer_ms"])
    if mitm_binary:
        os.environ["SANTA2026_MITM_BINARY"] = mitm_binary
    if joint_binary:
        os.environ["SANTA2026_JOINT_BINARY"] = joint_binary
    else:
        os.environ.pop("SANTA2026_JOINT_BINARY", None)
    global WORKER_CANDIDATES
    WORKER_CANDIDATES = [read_submission(path) for path in candidates]


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
            "recombine": ("recombine", "optimize_recombine"),
            "rustjoint": ("rust_joint", "optimize_rust_joint"),
            "mitm": ("mitm", "optimize_mitm"),
        }[strategy]
        function = getattr(importlib.import_module("santa2026." + module_name), function_name)
        for seed in seeds:
            started = time.monotonic()
            attempt = {"strategy": strategy, "seed": seed}
            try:
                if strategy in ("recombine", "rustjoint"):
                    sources = [best] + [source[puzzle.id] for source in WORKER_CANDIDATES]
                    sources = [[instruction[:] for instruction in program] for program in sources]
                    candidate = function(puzzle, sources, seed=seed, effort=effort)
                elif strategy in ("optimize", "milp", "polish", "mitm"):
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
    parser.add_argument("--strategies", nargs="+", choices=("shared", "synthesis", "optimize", "factor", "beam", "milp", "polish", "recombine", "rustjoint", "mitm"), required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0])
    parser.add_argument("--effort", type=int, default=1)
    parser.add_argument("--jobs", type=int, help="Default: 1 for mode-6 repair; up to 8 otherwise")
    parser.add_argument("--sample", type=int, default=100, help="0 evaluates all puzzles")
    parser.add_argument("--ids-file", type=Path, help="JSON list of exact puzzle IDs; overrides --sample")
    parser.add_argument("--beam-binary", type=Path)
    parser.add_argument("--repair-binary", type=Path, help="Override the default mode-6 repair engine")
    parser.add_argument("--mitm-binary", type=Path)
    parser.add_argument("--joint-binary", type=Path, help="Override the Rust joint-recipe optimizer")
    parser.add_argument("--joint-helpers", type=int, help="0..24; defaults to SANTA2026_JOINT_HELPERS or 0")
    parser.add_argument("--joint-plans", type=int, help="0..256; defaults to SANTA2026_JOINT_PLANS or 0")
    parser.add_argument("--joint-ms", type=int, help="Rust joint search budget; defaults to SANTA2026_JOINT_MS")
    parser.add_argument("--optimizer-ms", type=int, help="Native structural-search deadline; defaults to SANTA2026_OPTIMIZER_MS")
    parser.add_argument("--optimizer-boundary", type=int, choices=(0, 1), help="Prioritize exact retained cut boundary")
    parser.add_argument("--groups-direct", type=int, choices=(0, 1), help="Direct single-target private-cone resynthesis")
    parser.add_argument("--beam-mode", type=int, choices=(0, 1, 2, 3, 4), default=0)
    parser.add_argument("--repair-mode", type=int, choices=(6, 7, 8, 9), default=6)
    parser.add_argument("--repair-trials", type=int, default=12)
    parser.add_argument("--repair-passes", type=int, default=1)
    parser.add_argument("--candidates", nargs="*", type=Path, default=[])
    parser.add_argument("--resume-frozen", action="store_true", help="Resume the saved source/binary even if the checkout changed")
    args = parser.parse_args(argv)
    try:
        args.joint_helpers = args.joint_helpers if args.joint_helpers is not None else int(os.environ.get("SANTA2026_JOINT_HELPERS", "0"))
        args.joint_plans = args.joint_plans if args.joint_plans is not None else int(os.environ.get("SANTA2026_JOINT_PLANS", "0"))
        args.optimizer_boundary = args.optimizer_boundary if args.optimizer_boundary is not None else int(os.environ.get("SANTA2026_OPTIMIZER_BOUNDARY", "0"))
        args.groups_direct = args.groups_direct if args.groups_direct is not None else int(os.environ.get("SANTA2026_GROUPS_DIRECT", "0"))
        if args.optimizer_ms is None and os.environ.get("SANTA2026_OPTIMIZER_MS") is not None:
            args.optimizer_ms = int(os.environ["SANTA2026_OPTIMIZER_MS"])
        if args.joint_ms is None and os.environ.get("SANTA2026_JOINT_MS") is not None:
            args.joint_ms = int(os.environ["SANTA2026_JOINT_MS"])
    except ValueError:
        parser.error("native options must be integers")
    if not 0 <= args.joint_helpers <= 24 or args.optimizer_boundary not in (0, 1) or args.groups_direct not in (0, 1):
        parser.error("joint-helpers must be 0..24 and boundary/direct flags must be 0 or 1")
    if not 0 <= args.joint_plans <= 256:
        parser.error("joint-plans must be 0..256")
    if args.optimizer_ms is not None and not 1 <= args.optimizer_ms <= 600000:
        parser.error("optimizer-ms must be 1..600000")
    if args.joint_ms is not None and not 1 <= args.joint_ms <= 120000:
        parser.error("joint-ms must be 1..120000")
    if args.jobs is None:
        # The native repair dictionary can have a much larger RSS than the
        # Python constructions. Let explicit --jobs opt into parallel repair.
        args.jobs = 1 if "rustjoint" in args.strategies or ("polish" in args.strategies and args.repair_mode == 6) else min(os.cpu_count() or 1, 8)
    if min(args.jobs, args.effort, args.repair_trials, args.repair_passes) < 1:
        parser.error("jobs, effort and repair budgets must be positive")
    started = time.monotonic()
    puzzles = read_puzzles(args.puzzles)
    baseline = read_submission(args.baseline)
    costs = score_submission(puzzles, baseline)
    selected = select_puzzles(puzzles, costs, args.sample)
    if args.ids_file:
        identifiers = json.loads(args.ids_file.read_text())
        if not isinstance(identifiers, list) or any(not isinstance(item, str) for item in identifiers):
            raise ValueError("ids-file must contain a JSON list of puzzle IDs")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("ids-file contains duplicate puzzle IDs")
        lookup = {puzzle.id: puzzle for puzzle in puzzles}
        missing = set(identifiers) - set(lookup)
        if missing:
            raise ValueError("ids-file contains unknown puzzle IDs: " + ", ".join(sorted(missing)))
        selected = [lookup[identifier] for identifier in identifiers]
    if not selected:
        raise ValueError("no puzzles selected")
    args.output.mkdir(parents=True, exist_ok=True)
    configuration = {
        "puzzles_sha256": _hash(args.puzzles), "baseline_sha256": _hash(args.baseline),
        "strategies": args.strategies, "seeds": args.seeds, "effort": args.effort,
        "selected_ids": [puzzle.id for puzzle in selected],
        "python_version": sys.version.split()[0],
    }
    configuration["dependency_versions"] = {}
    if "polish" in args.strategies or "rustjoint" in args.strategies:
        configuration["native_options"] = {"optimizer_ms": args.optimizer_ms,
            "optimizer_boundary": args.optimizer_boundary, "joint_helpers": args.joint_helpers,
            "groups_direct": args.groups_direct}
        configuration["native_options"]["joint_plans"] = args.joint_plans
        configuration["native_options"]["joint_ms"] = args.joint_ms
    for dependency in ("numpy", "scipy", "ortools"):
        try:
            configuration["dependency_versions"][dependency] = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            configuration["dependency_versions"][dependency] = None
    if any(strategy in args.strategies for strategy in ("beam", "polish")):
        configuration["beam_mode"] = args.beam_mode
        if "polish" in args.strategies:
            configuration["polish_mode"] = args.repair_mode
            configuration["repair_trials"] = args.repair_trials
            configuration["repair_passes"] = args.repair_passes
    if "mitm" in args.strategies:
        configuration["repair_trials"] = args.repair_trials
    candidate_hashes = []
    for path in args.candidates:
        score_submission(puzzles, read_submission(path))
        candidate_hashes.append(_hash(path))
    if candidate_hashes:
        configuration["candidate_sha256"] = candidate_hashes
    if args.resume_frozen:
        configuration, signature, snapshot, executable = _load_frozen(args.output, configuration)
    else:
        source = REPOSITORY / "src"
        hashes = {str(path.relative_to(source)): _hash(path) for path in sorted((source / "santa2026").rglob("*.py"))}
        configuration["source_hashes"] = hashes
        code_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
        snapshot = args.output / "code" / code_id
        if not snapshot.exists():
            shutil.copytree(source / "santa2026", snapshot / "santa2026", ignore=shutil.ignore_patterns("__pycache__"))
        snapshot_hashes = {str(path.relative_to(snapshot)): _hash(path) for path in sorted(snapshot.rglob("*.py"))}
        if snapshot_hashes != hashes:
            raise ValueError("source changed while snapshotting; rerun after the edit finishes")
        executable = None
        if "beam" in args.strategies or ("polish" in args.strategies and args.repair_mode != 6):
            from santa2026.beam import binary_path
            original_binary = args.beam_binary.resolve() if args.beam_binary else binary_path()
            binary_hash = _hash(original_binary)
            configuration["beam_binary_sha256"] = binary_hash
            executable = args.output / "binaries" / binary_hash
            executable.parent.mkdir(exist_ok=True)
            if not executable.exists():
                shutil.copy2(original_binary, executable)
            if _hash(executable) != binary_hash:
                raise ValueError("binary changed while snapshotting; retry after compilation")
            executable = executable.resolve()
        if "polish" in args.strategies and args.repair_mode == 6:
            from santa2026.beam import binary_path
            original_repair = args.repair_binary or args.beam_binary or binary_path(repair=True)
            original_repair = original_repair.resolve()
            digest = _hash(original_repair)
            configuration["repair_binary_sha256"] = digest
            frozen_repair = args.output / "binaries" / digest
            frozen_repair.parent.mkdir(exist_ok=True)
            if not frozen_repair.exists():
                shutil.copy2(original_repair, frozen_repair)
            if _hash(frozen_repair) != digest:
                raise ValueError("repair binary changed while snapshotting")
        if "mitm" in args.strategies:
            from santa2026.mitm import binary_path as mitm_binary_path
            original_mitm = args.mitm_binary.resolve() if args.mitm_binary else mitm_binary_path()
            digest = _hash(original_mitm)
            configuration["mitm_binary_sha256"] = digest
            frozen_mitm = args.output / "binaries" / digest
            frozen_mitm.parent.mkdir(exist_ok=True)
            if not frozen_mitm.exists():
                shutil.copy2(original_mitm, frozen_mitm)
            if _hash(frozen_mitm) != digest:
                raise ValueError("exact-search binary changed while snapshotting")
        if "rustjoint" in args.strategies:
            from santa2026.rust_joint import binary_path as joint_binary_path
            original_joint = args.joint_binary.resolve() if args.joint_binary else joint_binary_path()
            digest = _hash(original_joint)
            configuration["joint_binary_sha256"] = digest
            frozen_joint = args.output / "binaries" / digest
            frozen_joint.parent.mkdir(exist_ok=True)
            if not frozen_joint.exists():
                shutil.copy2(original_joint, frozen_joint)
            if _hash(frozen_joint) != digest:
                raise ValueError("joint-recipe binary changed while snapshotting")
        signature = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        _atomic_json(args.output / "manifest.json", dict(configuration, signature=signature))
    candidate_files = []
    for index, digest in enumerate(configuration.get("candidate_sha256", [])):
        frozen_input = args.output / "inputs" / (digest + ".csv")
        if not args.resume_frozen and not frozen_input.exists():
            frozen_input.parent.mkdir(exist_ok=True)
            shutil.copyfile(args.candidates[index], frozen_input)
        if not frozen_input.is_file() or _hash(frozen_input) != digest:
            raise ValueError("frozen candidate program file is missing or damaged")
        candidate_files.append(str(frozen_input.resolve()))
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
    beam_binary = str(executable) if executable else None
    repair_binary = str((args.output / "binaries" / configuration["repair_binary_sha256"]).resolve()) if "repair_binary_sha256" in configuration else None
    mitm_binary = str((args.output / "binaries" / configuration["mitm_binary_sha256"]).resolve()) if "mitm" in args.strategies else None
    joint_binary = str((args.output / "binaries" / configuration["joint_binary_sha256"]).resolve()) if "joint_binary_sha256" in configuration else None
    worker_failure = None
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, initializer=_initialize,
                initargs=(str(snapshot.resolve()), beam_binary, args.beam_mode,
                          args.repair_mode, args.repair_trials, args.repair_passes, candidate_files, mitm_binary, repair_binary, joint_binary,
                          configuration.get("native_options", DEFAULT_NATIVE_OPTIONS))) as pool:
            futures = [pool.submit(_evaluate, task) for task in pending]
            for future in concurrent.futures.as_completed(futures):
                record = future.result()
                records[record["id"]] = record
                best_programs[record["id"]] = record["program"]
                _atomic_json(checkpoints / f"{record['id']}.json", record)
                if len(records) % 10 == 0 or len(records) == len(selected):
                    saved = sum(r["baseline_instructions"] - r["instructions"] for r in records.values())
                    print(f"Completed {len(records)}/{len(selected)}; saved={saved}; full_portfolio={original_score-saved}", flush=True)
    except BrokenProcessPool as exc:
        worker_failure = str(exc)
        print("A worker stopped. Saving validated progress; resume with --resume-frozen and fewer jobs.", flush=True)
    output_csv = args.output / "best.csv"
    write_submission(output_csv, best_programs)
    verified = score_submission(puzzles, read_submission(output_csv))
    final_score = sum(item.instructions for item in verified.values())
    report = {
        "signature": signature, "puzzles": len(puzzles), "evaluated": len(records), "selected": len(selected),
        "complete": len(records) == len(selected), "worker_failure": worker_failure,
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
            "attempts": records.get(puzzle.id, {}).get("attempts", []),
        } for puzzle in selected},
    }
    _atomic_json(args.output / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("per_puzzle", "errors")}, indent=2), flush=True)
    if report["errors"]:
        print(f"Strategy failures: {len(report['errors'])}; see report.json", flush=True)
    return 2 if worker_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
