#!/usr/bin/env python3
"""Bounded, interleaved quality experiments for the three Rust search methods."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from santa2026.beam import _settings
from santa2026.core import read_puzzles, read_submission, score_submission, validate_program, write_submission
from santa2026.native_output import read_program_output


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    temporary.replace(path)


def payload(puzzle, programs, joint=False):
    fields = ["puzzle", puzzle.value_limit, len(puzzle.starting_values), len(puzzle.targets),
              *puzzle.starting_values, *sorted(puzzle.targets)]
    if joint:
        fields.append(len(programs))
    for program in programs:
        fields.append(len(program))
        for instruction in program:
            fields.extend(instruction)
    return " ".join(map(str, fields)) + "\n"


def search(name, binary, puzzle, original, candidates, seconds, joint_helpers=0, tail_boundary=False, groups_direct=False, joint_plans=0, window_rename=False):
    best = [instruction[:] for instruction in original]
    generated = []
    lower_bound = len(set(puzzle.targets) - set(puzzle.starting_values))
    attempts = []
    started = time.perf_counter()
    deadline = started + seconds
    for index in range(64):
        remaining = deadline - time.perf_counter()
        if remaining < .03 or (name != "construct" and len(best) == lower_bound):
            break
        environment = os.environ.copy()
        if name in ("window", "construct"):
            milliseconds = max(1, int(min(2, remaining - .02) * 1000))
            if name == "window":
                command = [str(binary), str(index), "24", "32", "96", str(milliseconds)]
                command.append("1" if window_rename else "0")
                data = payload(puzzle, [best])
            else:
                command = [str(binary), str(index), "1", str(milliseconds)]
                data = payload(puzzle, [])
        elif name == "joint":
            milliseconds = max(1, int(min(2, remaining - .02) * 1000))
            command = [str(binary), str(index), "4000", "8", "1024", str(milliseconds)]
            if joint_helpers or joint_plans:
                command.append(str(joint_helpers))
            if joint_plans:
                command.append(str(joint_plans))
            data = payload(puzzle, [best, *candidates], joint=True)
        else:
            if name == "repair":
                width, alpha, done, recent, bits, seed = _settings(index, 1)
                trials, passes = 12, 1
            else:
                width, alpha, done, recent, bits, seed = 3, .18, 52, 56, 20, index
                trials, passes = 1, 1
                environment["SANTA2026_OPTIMIZER_MS"] = str(max(1, int(min(2, remaining - .02) * 1000)))
                environment["SANTA2026_OPTIMIZER_BOUNDARY"] = "1" if tail_boundary else "0"
                if name == "groups":
                    environment["SANTA2026_GROUPS_MS"] = environment["SANTA2026_OPTIMIZER_MS"]
                    environment["SANTA2026_GROUPS_DIRECT"] = "1" if groups_direct else "0"
            command = [str(binary), str(width), str(alpha), str(done), str(recent), str(bits),
                       "6", str(seed), str(trials), str(passes)]
            data = payload(puzzle, [best])
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        attempt_started = time.perf_counter()
        record = {"index": index, "command": command}
        program = None
        try:
            result = subprocess.run(command, input=data, text=True, capture_output=True,
                                    timeout=remaining, env=environment)
            record["returncode"] = result.returncode
            if result.stderr.strip():
                record["diagnostics"] = result.stderr.strip()[-2000:]
            if result.returncode:
                record["error"] = f"engine exited {result.returncode}: {result.stderr.strip()[-2000:]}"
            program = read_program_output(result.stdout, puzzle, require_complete=bool(result.returncode))
            if program is None and not result.returncode:
                raise ValueError("missing native program response")
            if program is not None and result.returncode:
                record["recovered_complete_output"] = True
        except subprocess.TimeoutExpired as error:
            record["budget_exhausted"] = True
            try:
                program = read_program_output(error.stdout, puzzle, require_complete=True)
                if program is not None:
                    record["recovered_complete_output"] = True
            except (ValueError, TypeError, RecursionError) as exc:
                record["error"] = "invalid complete timed output: " + str(exc)
        except (ValueError, TypeError, RecursionError) as exc:
            record["error"] = str(exc)
        if program is not None:
            record["instructions"] = len(program)
            if name == "construct" and program not in generated:
                generated.append(program)
                generated.sort(key=len)
                generated = generated[:3]
            if len(program) < len(best):
                best = program
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        record["wall_seconds"] = time.perf_counter() - attempt_started
        record["cpu_seconds"] = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
        attempts.append(record)
        if record.get("budget_exhausted"):
            break
    validate_program(puzzle, best)
    return {"id": puzzle.id, "engine": name, "baseline_instructions": len(original),
            "instructions": len(best), "program": best, "attempts": attempts,
            "wall_seconds": time.perf_counter() - started,
            "cpu_seconds": sum(a["cpu_seconds"] for a in attempts),
            "seconds_budget": seconds, "generated_programs": generated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puzzles", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--ids-file", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, nargs="*", default=[])
    parser.add_argument("--repair", type=Path, default=Path("outputs/bin/repair-rust"))
    parser.add_argument("--tail", type=Path, default=Path("outputs/bin/optimizer-rust"))
    parser.add_argument("--joint", type=Path, default=Path("outputs/bin/joint-rust"))
    parser.add_argument("--groups", type=Path, default=Path("outputs/bin/factor-groups-rust"))
    parser.add_argument("--window", type=Path, default=Path("outputs/bin/window-rust"))
    parser.add_argument("--construct", type=Path, default=Path("outputs/bin/construct-rust"))
    parser.add_argument("--seconds", type=float, default=4)
    parser.add_argument("--methods", choices=("repair", "tail", "joint", "groups", "window", "construct"), nargs="+", default=["repair", "tail", "joint"])
    parser.add_argument("--joint-helpers", type=int, default=0)
    parser.add_argument("--joint-plans", type=int, default=0)
    parser.add_argument("--tail-boundary", action="store_true")
    parser.add_argument("--groups-direct", action="store_true", help="Resynthesize one complete private cone from safe outside context")
    parser.add_argument("--window-rename", action="store_true", help="Match physical live-outs up to a bijective suffix register renaming")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    if len(set(args.methods)) != len(args.methods) or args.joint_helpers < 0 or not 0 <= args.joint_plans <= 256:
        parser.error("methods must be unique and helper budget nonnegative")
    puzzles = read_puzzles(args.puzzles)
    by_id = {p.id: p for p in puzzles}
    baseline = read_submission(args.baseline)
    score_submission(puzzles, baseline)
    ids = json.loads(args.ids_file.read_text())
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
        raise ValueError("ids-file must be a nonempty JSON list of puzzle IDs")
    if len(set(ids)) != len(ids) or set(ids) - set(by_id):
        raise ValueError("duplicate or unknown puzzle IDs")
    candidates = [read_submission(path) for path in args.candidates]
    for programs in candidates:
        score_submission(puzzles, programs)
    args.output.mkdir(parents=True, exist_ok=True)
    engines = {name: getattr(args, name).resolve() for name in args.methods}
    configuration = {"puzzles_sha256": digest(args.puzzles), "baseline_sha256": digest(args.baseline),
                     "candidate_sha256": [digest(path) for path in args.candidates], "ids": ids,
                     "seconds_per_puzzle_engine": args.seconds,
                     "methods": args.methods, "joint_helpers": args.joint_helpers, "tail_boundary": args.tail_boundary,
                     "groups_direct": args.groups_direct,
                     "joint_plans": args.joint_plans,
                     "window_rename": args.window_rename,
                     "trace_environment": {name: os.environ.get(name) == "1" for name in
                         ("SANTA2026_JOINT_TRACE", "SANTA2026_OPTIMIZER_TRACE", "SANTA2026_GROUPS_TRACE", "SANTA2026_WINDOW_TRACE")},
                     "binaries": {name: digest(path) for name, path in engines.items()},
                     "script_sha256": digest(Path(__file__)),
                     "core_sha256": digest(ROOT / "src/santa2026/core.py"),
                     "output_parser_sha256": digest(ROOT / "src/santa2026/native_output.py"),
                     "settings_sha256": digest(ROOT / "src/santa2026/beam.py")}
    signature = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    atomic_json(args.output / "manifest.json", dict(configuration, signature=signature))
    code_directory = args.output / "code" / signature
    code_directory.mkdir(parents=True, exist_ok=True)
    for filename in (Path(__file__), ROOT / "src/santa2026/core.py", ROOT / "src/santa2026/beam.py",
                     ROOT / "src/santa2026/native_output.py"):
        shutil.copy2(filename, code_directory / filename.name)
    for name, source in engines.items():
        destination = args.output / "binaries" / configuration["binaries"][name]
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)
        if digest(destination) != configuration["binaries"][name]:
            raise ValueError("frozen engine damaged")
        engines[name] = destination.resolve()
    names = list(engines)
    records = {name: [] for name in names}
    for number, identifier in enumerate(ids):
        offset = number % len(names)
        for name in names[offset:] + names[:offset]:
            path = args.output / "checkpoints" / f"{name}-{identifier}.json"
            record = json.loads(path.read_text()) if path.exists() else None
            if record is None or record.get("signature") != signature:
                record = search(name, engines[name], by_id[identifier], baseline[identifier],
                                [programs[identifier] for programs in candidates], args.seconds,
                                args.joint_helpers, args.tail_boundary, args.groups_direct, args.joint_plans,
                                args.window_rename)
                record["signature"] = signature
                atomic_json(path, record)
            if record["id"] != identifier or record["engine"] != name:
                raise ValueError("checkpoint identifies a different puzzle or engine")
            validate_program(by_id[identifier], record["program"])
            if len(record["program"]) > len(baseline[identifier]):
                raise ValueError("checkpoint regresses the baseline")
            records[name].append(record)
            print(f"{number+1}/{len(ids)} {name} {identifier}: {record['baseline_instructions']}->{record['instructions']} ({record['wall_seconds']:.2f}s)", flush=True)
    summaries = {}
    combined = dict(baseline)
    for name, rows in records.items():
        programs = dict(baseline)
        for row in rows:
            programs[row["id"]] = row["program"]
            if len(row["program"]) < len(combined[row["id"]]):
                combined[row["id"]] = row["program"]
        output = args.output / f"{name}.csv"
        write_submission(output, programs)
        scores = score_submission(puzzles, read_submission(output))
        summaries[name] = {"score": sum(r.instructions for r in scores.values()),
                           "saved": sum(r["baseline_instructions"] - r["instructions"] for r in rows),
                           "improved": sum(r["instructions"] < r["baseline_instructions"] for r in rows),
                           "wall_seconds": sum(r["wall_seconds"] for r in rows),
                           "cpu_seconds": sum(r["cpu_seconds"] for r in rows),
                           "errors": [dict(id=r["id"], **a) for r in rows for a in r["attempts"] if "error" in a],
                           "budget_exhaustions": sum(bool(a.get("budget_exhausted")) for r in rows for a in r["attempts"])}
        if name == "construct":
            raw_rows = [row for row in rows if row.get("generated_programs")]
            summaries[name]["generated_cases"] = len(raw_rows)
            summaries[name]["standalone_selected_instructions"] = sum(len(r["generated_programs"][0]) for r in raw_rows)
            summaries[name]["baseline_for_generated_cases"] = sum(r["baseline_instructions"] for r in raw_rows)
            summaries[name]["raw_portfolios"] = []
            for variant in range(3):
                raw = dict(baseline)
                for row in raw_rows:
                    choices = row["generated_programs"]
                    raw[row["id"]] = choices[min(variant, len(choices) - 1)]
                raw_output = args.output / f"construct-generated-{variant}.csv"
                write_submission(raw_output, raw)
                raw_scores = score_submission(puzzles, read_submission(raw_output))
                summaries[name]["raw_portfolios"].append({"path": str(raw_output),
                    "score": sum(item.instructions for item in raw_scores.values()),
                    "sha256": digest(raw_output)})
    write_submission(args.output / "best.csv", combined)
    combined_score = sum(r.instructions for r in score_submission(puzzles, read_submission(args.output / "best.csv")).values())
    atomic_json(args.output / "report.json", {"signature": signature, "selected": len(ids),
                "baseline_score": sum(map(len, baseline.values())), "score": combined_score, "engines": summaries})
    print(json.dumps({"score": combined_score, "engines": summaries}, indent=2), flush=True)


if __name__ == "__main__":
    main()
