#!/usr/bin/env python3
"""Compare identical mode-6 repair workloads across compiled language ports.

This script never builds engines, launches parallel searches, submits programs,
or changes Git. Every measured engine runs alone. Fixed-time experiments repeat
exactly the supplied seed sequence; repeated work is counted separately from
unique puzzle/seed pairs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from santa2026.core import read_puzzles, read_submission, score_submission, validate_program, write_submission

_lab_spec = importlib.util.spec_from_file_location("_language_comparison_lab", ROOT / "scripts" / "lab.py")
_lab = importlib.util.module_from_spec(_lab_spec)
_lab_spec.loader.exec_module(_lab)
select_puzzles = _lab.select_puzzles


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def latin_square_order(names, repeat):
    offset = repeat % len(names)
    return names[offset:] + names[:offset]


def encode_batch(puzzles, baseline):
    lines = []
    aliases = {}
    for index, puzzle in enumerate(puzzles):
        identifier = f"p_{index:05d}"
        aliases[identifier] = puzzle
        lines.append(" ".join(map(str, (identifier, puzzle.value_limit, len(puzzle.starting_values),
                                       len(puzzle.targets), *puzzle.starting_values, *puzzle.targets))))
        program = baseline[puzzle.id]
        lines.append(str(len(program)))
        lines.extend(" ".join(map(str, instruction)) for instruction in program)
    return ("\n".join(lines) + "\n").encode("utf-8"), aliases


def parse_output(text, aliases, require_complete=True, complete_lines_only=False):
    discarded = 0
    if complete_lines_only and text and not text.endswith("\n"):
        cut = text.rfind("\n") + 1
        discarded = len(text[cut:].encode("utf-8"))
        text = text[:cut]
    programs = {}
    seen = set()
    duplicate = []
    extra = []
    invalid = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        pieces = line.split(None, 1)
        if len(pieces) != 2:
            invalid.append({"line": line_number, "error": "expected identifier and JSON program"})
            continue
        identifier, cell = pieces
        if identifier not in aliases:
            extra.append(identifier)
            continue
        if identifier in seen:
            duplicate.append(aliases[identifier].id)
            continue
        seen.add(identifier)
        puzzle = aliases[identifier]
        try:
            program = json.loads(cell)
            validate_program(puzzle, program)
        except (ValueError, TypeError, RecursionError) as exc:
            invalid.append({"line": line_number, "id": puzzle.id, "error": str(exc)})
            continue
        programs[puzzle.id] = program
    missing = [puzzle.id for puzzle in aliases.values() if puzzle.id not in programs]
    report = {
        "valid": not (duplicate or extra or invalid or (require_complete and missing)),
        "complete": not missing,
        "expected_programs": len(aliases), "valid_programs": len(programs),
        "missing_ids": missing, "extra_ids": extra, "duplicate_ids": duplicate,
        "invalid_programs": invalid, "discarded_partial_bytes": discarded,
    }
    return programs, report


def _time_metrics(stderr):
    result = {"peak_rss_bytes": None, "time_wall_seconds": None, "time_cpu_seconds": None}
    usage = re.findall(r"([0-9.]+)\s+real\s+([0-9.]+)\s+user\s+([0-9.]+)\s+sys", stderr)
    if usage:
        wall, user, system = map(float, usage[-1])
        result.update(time_wall_seconds=wall, time_cpu_seconds=user + system)
    rss = re.findall(r"^\s*(\d+)\s+maximum resident set size\s*$", stderr, re.MULTILINE)
    if rss:
        # BSD time on macOS reports bytes, unlike Linux's ru_maxrss.
        result["peak_rss_bytes"] = int(rss[-1])
    return result


def run_batch(binary, payload, aliases, output, config, seed, timeout, allow_partial=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = output.with_suffix(".stdout")
    stderr_path = output.with_suffix(".stderr")
    command = [str(binary), str(config["width"]), str(config["alpha"]), str(config["n_done"]),
               str(config["n_recent"]), str(config["index_bits"]), "6", str(seed),
               str(config["trials"]), str(config["passes"])]
    use_bsd_time = sys.platform == "darwin" and Path("/usr/bin/time").is_file()
    measured_command = ["/usr/bin/time", "-l", *command] if use_bsd_time else command
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    timed_out = False
    forced_kill = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(measured_command, stdin=subprocess.PIPE, stdout=stdout,
                                   stderr=stderr, start_new_session=True)
        try:
            process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # BSD time ignores SIGINT while waiting, and can still report the
            # terminated child's CPU/RSS. A hard group kill is the fallback.
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                forced_kill = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
    elapsed = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    time_data = _time_metrics(stderr) if use_bsd_time else _time_metrics("")
    try:
        text = stdout_path.read_text(encoding="utf-8")
        programs, validation = parse_output(text, aliases, require_complete=not allow_partial,
                                            complete_lines_only=allow_partial and timed_out)
    except UnicodeDecodeError as exc:
        programs = {}
        validation = {"valid": False, "complete": False, "expected_programs": len(aliases),
                      "valid_programs": 0, "missing_ids": [p.id for p in aliases.values()],
                      "extra_ids": [], "duplicate_ids": [],
                      "invalid_programs": [{"error": f"invalid UTF-8: {exc}"}], "discarded_partial_bytes": 0}
    if (not allow_partial and timed_out) or (process.returncode != 0 and not timed_out):
        validation["valid"] = False
    cpu_delta = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    record = {
        "seed": seed, "command": command, "returncode": process.returncode,
        "timed_out": timed_out, "forced_kill": forced_kill,
        "wall_seconds": elapsed,
        "cpu_seconds": time_data["time_cpu_seconds"] if timed_out and time_data["time_cpu_seconds"] is not None else cpu_delta,
        "cpu_measurement": "bsd_time" if timed_out and time_data["time_cpu_seconds"] is not None else "waited_child_rusage",
        **time_data, "validation": validation,
        "stdout": str(stdout_path), "stderr": str(stderr_path),
        "program_lengths": {identifier: len(program) for identifier, program in programs.items()},
        "program_sha256": {identifier: hashlib.sha256(json.dumps(program, separators=(",", ":")).encode()).hexdigest()
                           for identifier, program in programs.items()},
    }
    if forced_kill and time_data["time_cpu_seconds"] is None:
        record["cpu_seconds"] = None
        record["cpu_measurement"] = "unavailable_after_forced_group_kill"
    _atomic_json(output.with_suffix(".json"), record)
    return programs, record


def merge_validated(puzzles, baseline, improvements, path):
    merged = {identifier: [instruction[:] for instruction in program] for identifier, program in baseline.items()}
    by_id = {puzzle.id: puzzle for puzzle in puzzles}
    for identifier, program in improvements.items():
        validate_program(by_id[identifier], program)
        if len(program) < len(merged[identifier]):
            merged[identifier] = [instruction[:] for instruction in program]
    write_submission(path, merged)
    replayed = read_submission(path)
    scores = score_submission(puzzles, replayed)
    return sum(result.instructions for result in scores.values())


def _best_into(best, programs):
    for identifier, program in programs.items():
        if identifier not in best or len(program) < len(best[identifier]):
            best[identifier] = program


def _fixed_run(name, binary, puzzles, selected, baseline, payload, aliases, output, config, seeds, timeout):
    best = {}
    batches = []
    for index, seed in enumerate(seeds):
        programs, record = run_batch(binary, payload, aliases, output / f"batch-{index:03d}-seed-{seed}",
                                     config, seed, timeout)
        _best_into(best, programs)
        batches.append(record)
    selected_baseline = sum(len(baseline[p.id]) for p in selected)
    full_baseline = sum(len(program) for program in baseline.values())
    complete = all(p.id in best for p in selected)
    raw_score = sum(len(best[p.id]) for p in selected) if complete else None
    portfolio_score = merge_validated(puzzles, baseline, best, output / "portfolio.csv")
    peaks = [b["peak_rss_bytes"] for b in batches if b["peak_rss_bytes"] is not None]
    report = {
        "engine": name, "all_outputs_valid": all(b["validation"]["valid"] for b in batches),
        "selected_puzzles": len(selected), "puzzle_seed_attempts": len(selected) * len(seeds),
        "wall_seconds": sum(b["wall_seconds"] for b in batches),
        "cpu_seconds": sum(b["cpu_seconds"] for b in batches) if all(b["cpu_seconds"] is not None for b in batches) else None,
        "peak_rss_bytes": max(peaks) if peaks else None,
        "raw_selected_score": raw_score,
        "raw_complete_portfolio_score": full_baseline - selected_baseline + raw_score if raw_score is not None else None,
        "validated_portfolio_score": portfolio_score,
        "instructions_saved": full_baseline - portfolio_score,
        "batches": batches,
    }
    _atomic_json(output / "report.json", report)
    return report


def _budget_run(name, binary, puzzles, selected, baseline, payload, aliases, output, config, seeds, seconds):
    best = {}
    records = []
    attempts = 0
    unique_pairs = set()
    started = time.perf_counter()
    deadline = started + seconds
    index = 0
    while time.perf_counter() < deadline:
        seed = seeds[index % len(seeds)]
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        programs, record = run_batch(binary, payload, aliases, output / f"batch-{index:04d}-seed-{seed}",
                                     config, seed, remaining, allow_partial=True)
        attempts += len(programs)
        unique_pairs.update((identifier, seed) for identifier in programs)
        _best_into(best, programs)
        records.append(record)
        index += 1
        if record["timed_out"] or record["returncode"] != 0:
            break
    elapsed = time.perf_counter() - started
    portfolio_score = merge_validated(puzzles, baseline, best, output / "portfolio.csv")
    peaks = [b["peak_rss_bytes"] for b in records if b["peak_rss_bytes"] is not None]
    report = {
        "engine": name, "budget_seconds": seconds, "elapsed_including_control_seconds": elapsed,
        "engine_wall_seconds": sum(b["wall_seconds"] for b in records),
        "cpu_seconds": sum(b["cpu_seconds"] for b in records) if all(b["cpu_seconds"] is not None for b in records) else None,
        "peak_rss_bytes": max(peaks) if peaks else None,
        "valid_completed_puzzle_attempts": attempts, "unique_puzzle_seed_pairs": len(unique_pairs),
        "unique_puzzles": len(best), "seed_sequence": list(seeds),
        "seed_sequence_rule": "cycle exactly the supplied seeds; repeated seed 0 is repeated work",
        "validated_portfolio_score": portfolio_score,
        "instructions_saved": sum(len(p) for p in baseline.values()) - portfolio_score,
        "all_received_programs_valid": all(b["validation"]["valid"] for b in records),
        "batches": records,
    }
    _atomic_json(output / "report.json", report)
    return report


def fixed_differential(fixed, names, repeats, seed_count):
    differences = []
    all_valid = all(run["all_outputs_valid"] for runs in fixed.values() for run in runs)
    for repeat in range(repeats):
        by_name = {name: fixed[name][repeat] for name in names}
        for seed_index in range(seed_count):
            ids = sorted(set().union(*(set(run["batches"][seed_index]["program_lengths"]) for run in by_name.values())))
            for identifier in ids:
                lengths = {name: run["batches"][seed_index]["program_lengths"].get(identifier) for name, run in by_name.items()}
                hashes = {name: run["batches"][seed_index]["program_sha256"].get(identifier) for name, run in by_name.items()}
                if len(set(lengths.values())) > 1 or len(set(hashes.values())) > 1:
                    differences.append({"repeat": repeat, "seed_index": seed_index, "id": identifier,
                                        "lengths": lengths, "program_sha256": hashes,
                                        "instruction_counts_match": len(set(lengths.values())) == 1})
    variability = {}
    for name in names:
        first = [batch["program_sha256"] for batch in fixed[name][0]["batches"]]
        variability[name] = any([batch["program_sha256"] for batch in run["batches"]] != first for run in fixed[name][1:])
    counts_match = not any(not item["instruction_counts_match"] for item in differences)
    exact_match = not differences and not any(variability.values())
    eligible = all_valid and counts_match and exact_match and repeats >= 3 and len(names) >= 2
    return {"all_outputs_valid": all_valid, "instruction_counts_match": counts_match,
            "identical_program_streams": exact_match, "within_engine_variability": variability,
            "eligible_for_language_speed_claim": eligible, "differences": differences}


def _freeze_engines(specifications, output, build_metadata):
    engines = {}
    metadata_files = dict(item.split(":", 1) for item in build_metadata)
    for specification in specifications:
        if ":" not in specification:
            raise ValueError("engine must be specified as name:path")
        name, filename = specification.split(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in engines:
            raise ValueError("engine names must be unique simple identifiers")
        source = Path(filename).resolve()
        if not source.is_file() or not os.access(source, os.X_OK):
            raise ValueError(f"engine is missing or not executable: {source}")
        digest = _hash(source)
        frozen = output / "engines" / name / digest
        frozen.parent.mkdir(parents=True, exist_ok=True)
        if not frozen.exists():
            shutil.copy2(source, frozen)
        if _hash(frozen) != digest:
            raise ValueError("an engine changed while freezing it; finish the build first")
        metadata_path = Path(metadata_files[name]) if name in metadata_files else source.with_suffix(".json")
        metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else None
        if metadata is not None and metadata.get("binary_sha256") != digest:
            raise ValueError(f"build metadata does not identify the measured binary: {metadata_path}")
        engines[name] = {"original_path": str(source), "frozen_path": str(frozen.resolve()),
                         "binary_sha256": digest, "binary_bytes": source.stat().st_size,
                         "build_metadata_path": str(metadata_path) if metadata_path.is_file() else None,
                         "build_metadata": metadata}
    return engines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puzzles", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", required=True, help="cpp:path rust:path go:path")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=100, help="0 uses all puzzles")
    parser.add_argument("--seeds", nargs="+", type=int, default=[0])
    parser.add_argument("--trials", type=int, default=12)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--width", type=int, default=3)
    parser.add_argument("--alpha", type=float, default=.18)
    parser.add_argument("--n-done", type=int, default=52)
    parser.add_argument("--n-recent", type=int, default=56)
    parser.add_argument("--index-bits", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--budget-seconds", type=float, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--build-metadata", nargs="*", default=[], help="optional name:path JSON overrides")
    args = parser.parse_args(argv)
    if args.sample < 0 or min(args.trials, args.passes, args.width, args.repeats) < 1:
        parser.error("sample must be nonnegative; trials/passes/width/repeats must be positive")
    if args.budget_seconds < 0 or args.timeout_seconds <= 0:
        parser.error("budgets must be nonnegative and timeout must be positive")
    if min(args.n_done, args.n_recent) < 0 or not 1 <= args.index_bits <= 30:
        parser.error("history limits must be nonnegative and index-bits must be between 1 and 30")
    if not 0 <= args.alpha < float("inf") or min(args.seeds) < 0:
        parser.error("alpha must be finite and nonnegative; seeds must be nonnegative")
    args.output.mkdir(parents=True, exist_ok=True)
    puzzles = read_puzzles(args.puzzles)
    baseline = read_submission(args.baseline)
    costs = score_submission(puzzles, baseline)
    selected = select_puzzles(puzzles, costs, args.sample)
    if not selected:
        raise ValueError("no puzzles selected")
    engines = _freeze_engines(args.engines, args.output, args.build_metadata)
    names = list(engines)
    config = {name: getattr(args, name) for name in ("width", "alpha", "n_done", "n_recent", "index_bits", "trials", "passes")}
    payload, aliases = encode_batch(selected, baseline)
    manifest = {"puzzles_sha256": _hash(args.puzzles), "baseline_sha256": _hash(args.baseline),
                "baseline_score": sum(value.instructions for value in costs.values()),
                "selected_ids": [p.id for p in selected], "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "config": config, "mode": 6, "seeds": args.seeds, "repeats": args.repeats,
                "language_orders": [latin_square_order(names, repeat) for repeat in range(args.repeats)],
                "engines": engines, "python_version": platform.python_version(),
                "platform": platform.platform(), "logical_cpu_count": os.cpu_count(),
                "timing_scope": "one native engine process per seed; startup, parsing, search, output included; validation excluded",
                "concurrency": "one engine process at a time; caller must pause other search workloads",
                "benchmark_script_sha256": _hash(Path(__file__))}
    _atomic_json(args.output / "manifest.json", manifest)
    warmups = {}
    warm_puzzle = selected[len(selected) // 2]
    warm_payload, warm_aliases = encode_batch([warm_puzzle], baseline)
    for name in names:
        _, warmups[name] = run_batch(Path(engines[name]["frozen_path"]), warm_payload, warm_aliases,
                                    args.output / "warmup" / name, config, args.seeds[0], args.timeout_seconds)
        print(f"Warmup {name}: valid={warmups[name]['validation']['valid']}", flush=True)
    fixed = {name: [] for name in names}
    for repeat in range(args.repeats):
        for name in latin_square_order(names, repeat):
            result = _fixed_run(name, Path(engines[name]["frozen_path"]), puzzles, selected, baseline, payload, aliases,
                                args.output / "fixed" / f"repeat-{repeat}" / name, config, args.seeds, args.timeout_seconds)
            fixed[name].append(result)
            print(f"Fixed repeat={repeat} {name}: {result['wall_seconds']:.3f}s raw={result['raw_selected_score']} valid={result['all_outputs_valid']}", flush=True)
    differential = fixed_differential(fixed, names, args.repeats, len(args.seeds))
    differential["all_outputs_valid"] = differential["all_outputs_valid"] and all(r["validation"]["valid"] for r in warmups.values())
    differential["eligible_for_language_speed_claim"] &= differential["all_outputs_valid"]
    summaries = {}
    for name, runs in fixed.items():
        cpu = [run["cpu_seconds"] for run in runs if run["cpu_seconds"] is not None]
        rss = [run["peak_rss_bytes"] for run in runs if run["peak_rss_bytes"] is not None]
        summaries[name] = {"wall_median_seconds": statistics.median(run["wall_seconds"] for run in runs),
                           "cpu_median_seconds": statistics.median(cpu) if cpu else None,
                           "peak_rss_bytes": max(rss) if rss else None,
                           "raw_selected_scores": [run["raw_selected_score"] for run in runs],
                           "validated_portfolio_scores": [run["validated_portfolio_score"] for run in runs]}
    fastest = min(names, key=lambda name: summaries[name]["wall_median_seconds"])
    recommendation = {"fastest_measured_engine": fastest,
                      "eligible_winner": fastest if differential["eligible_for_language_speed_claim"] else None,
                      "qualification": "Measured result for this fixed workload, compiler configuration and machine; not a general language ranking."}
    if not differential["eligible_for_language_speed_claim"]:
        recommendation["qualification"] += " Output mismatch, nondeterminism, invalid output, fewer than three repeats or fewer than two engines prevents a pure language-speed claim."
    budgets = {}
    if args.budget_seconds:
        for name in names:
            budgets[name] = _budget_run(name, Path(engines[name]["frozen_path"]), puzzles, selected, baseline, payload, aliases,
                                        args.output / "budget" / name, config, args.seeds, args.budget_seconds)
            print(f"Budget {name}: attempts={budgets[name]['valid_completed_puzzle_attempts']} score={budgets[name]['validated_portfolio_score']}", flush=True)
    report = {"manifest": manifest, "warmups": warmups, "fixed_runs": fixed,
              "fixed_summary": summaries, "differential": differential,
              "recommendation": recommendation, "fixed_time": budgets}
    _atomic_json(args.output / "report.json", report)
    print(json.dumps({"fixed_summary": summaries, "recommendation": recommendation,
                      "quality_mismatches": len(differential["differences"])}, indent=2), flush=True)
    return 0 if differential["all_outputs_valid"] and differential["instruction_counts_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
