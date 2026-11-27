#!/usr/bin/env python3
"""Refresh the local best submission from public program banks. This script never submits anything.

1. Download each Kaggle dataset listed in ``--datasets`` into its own new directory and record its license.
2. Replay every ``id,program`` row exactly and keep the shortest valid program per puzzle.
3. Write a candidate CSV, a provenance report and the list of puzzles that changed (input for fresh search).

The downloaded files are untrusted data: only their CSV rows are read, nothing in them is executed.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from merge_banks import merge_banks, total_instructions
from santa2026.core import read_puzzles, read_submission, score_submission, write_submission

REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*")


def read_references(path: Path) -> list[str]:
    references = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not REFERENCE.fullmatch(line):
            raise ValueError(f"not a Kaggle dataset reference: {line!r}")
        references.append(line)
    return references


def download(kaggle: str, reference: str, directory: Path) -> dict:
    directory.mkdir(parents=True)
    entry = {"dataset": reference, "directory": str(directory), "licenses": [], "error": None}
    try:
        for command in (["download", reference, "-p", str(directory), "--unzip", "-q"],
                        ["metadata", reference, "-p", str(directory)]):
            subprocess.run([kaggle, "datasets", *command], check=True, capture_output=True, text=True, timeout=600)
        metadata = json.loads((directory / "dataset-metadata.json").read_text())
        entry["licenses"] = [item["name"] for item in metadata["info"]["licenses"]]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        entry["error"] = f"{type(error).__name__}: {error}"[:500]
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kaggle", default=shutil.which("kaggle"), help="path to an authenticated Kaggle CLI")
    parser.add_argument("--datasets", type=Path, default=ROOT / "scripts" / "public_banks.txt")
    parser.add_argument("--puzzles", type=Path, default=ROOT / "data" / "puzzles.csv")
    parser.add_argument("--best", type=Path, default=ROOT / "outputs" / "best.csv",
                        help="current best complete submission; replaced only when the score improves")
    parser.add_argument("--reuse", type=Path, help="merge an already downloaded bank directory instead of downloading")
    args = parser.parse_args(argv)
    if not args.best.is_file():
        parser.error(f"{args.best} does not exist; copy a complete validated submission there first")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = ROOT / "outputs" / "frontier" / stamp
    run.mkdir(parents=True)
    downloads = []
    if args.reuse:
        banks = args.reuse
    else:
        if not args.kaggle:
            parser.error("Kaggle CLI not found; pass --kaggle /path/to/kaggle")
        banks = ROOT / "data" / "public" / "research" / f"banks-{stamp}"
        for reference in read_references(args.datasets):
            entry = download(args.kaggle, reference, banks / reference.replace("/", "_"))
            downloads.append(entry)
            print(f"{reference}: {entry['error'] or entry['licenses']}", flush=True)

    puzzles = read_puzzles(args.puzzles)
    start = read_submission(args.best)
    score_submission(puzzles, start)
    best, per_file, source = merge_banks(puzzles, start, [banks])
    candidate = run / "candidate.csv"
    write_submission(candidate, best)
    verified = score_submission(puzzles, read_submission(candidate))
    score = sum(result.instructions for result in verified.values())
    changed = sorted(source)
    (run / "changed_ids.txt").write_text("".join(f"{pid}\n" for pid in changed))
    supplied = collections.Counter(str(Path(path).relative_to(banks)) for path in source.values())
    report = {"start_score": total_instructions(start), "score": score, "changed_puzzles": len(changed),
              "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
              "bank_directory": str(banks), "downloads": downloads,
              "puzzles_supplied_by_file": dict(supplied.most_common()), "improved_by": source, "files": per_file}
    (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if score < report["start_score"]:
        temporary = args.best.with_suffix(".tmp")
        shutil.copyfile(candidate, temporary)
        temporary.replace(args.best)
    print(json.dumps({key: report[key] for key in ("start_score", "score", "changed_puzzles", "candidate_sha256")}, indent=2))
    print(f"report: {run / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
