#!/usr/bin/env python3
"""Collect alternative starting programs ("lineages") for the annealing search from downloaded public banks.

A lineage is a valid program for a puzzle that is at most ``--max-excess`` instructions longer than the current
best and computes a different program. Those sharing the fewest values with the best come first. Output is the
JSON-lines format read by goodpjw2008's tri_driver.py in ``--mode lineage``. Bank files are read as data only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from merge_banks import candidate_rows
from santa2026.core import read_puzzles, read_submission, score_submission, validate_program

OPERATIONS = {"+": int.__add__, "-": int.__sub__, "*": int.__mul__, "/": int.__floordiv__}


def computed_values(puzzle, program) -> set[int]:
    """Values written by an already validated program."""
    registers = list(puzzle.starting_values) + [None] * (puzzle.registers - len(puzzle.starting_values))
    values = set()
    for operation, left, right, destination in program:
        registers[destination] = OPERATIONS[operation](registers[left], registers[right])
        values.add(registers[destination])
    return values


def collect_lineages(puzzles, best, roots, max_excess=6, per_puzzle=4) -> list[dict]:
    by_id = {puzzle.id: puzzle for puzzle in puzzles}
    found: dict[str, dict[str, tuple]] = {}
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.csv"))
        for path in files:
            for pid, cell in candidate_rows(path):
                if pid not in by_id:
                    continue
                try:
                    program = json.loads(cell)
                    validate_program(by_id[pid], program)
                except (ValueError, TypeError, RecursionError):
                    continue
                if len(best[pid]) <= len(program) <= len(best[pid]) + max_excess and program != best[pid]:
                    found.setdefault(pid, {}).setdefault(json.dumps(program), (program, f"{path.parent.name}/{path.name}"))
    lineages = []
    for pid in sorted(found):
        reference = computed_values(by_id[pid], best[pid])
        ranked = []
        for program, origin in found[pid].values():
            values = computed_values(by_id[pid], program)
            overlap = len(values & reference) / max(1, len(values | reference))
            ranked.append((overlap, len(program), origin, program))
        ranked.sort(key=lambda item: item[:3])
        for overlap, length, origin, program in ranked[:per_puzzle]:
            lineages.append({"k": pid, "len": length, "best": len(best[pid]), "ov": round(overlap, 3),
                             "p": program, "src": origin})
    return lineages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--puzzles", type=Path, default=ROOT / "data" / "puzzles.csv")
    parser.add_argument("--best", type=Path, default=ROOT / "outputs" / "best.csv")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-excess", type=int, default=6)
    parser.add_argument("--per-puzzle", type=int, default=4)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args(argv)
    puzzles = read_puzzles(args.puzzles)
    best = read_submission(args.best)
    score_submission(puzzles, best)
    lineages = collect_lineages(puzzles, best, args.roots, args.max_excess, args.per_puzzle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(item, separators=(",", ":")) + "\n" for item in lineages))
    print(f"puzzles with lineages {len({item['k'] for item in lineages})}, lineages written {len(lineages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
