#!/usr/bin/env python3
"""Follow a running triwalk pass: one line per finished puzzle, total gain and estimated time left. Read only."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def finished_runs(stats: Path, tag: str):
    """Rows of work/tri_stats.csv: time,mode,tag,puzzle,config,seconds,length_before,length_after,best_before."""
    if not stats.is_file():
        return []
    runs = []
    for line in stats.read_text().split("\n")[:-1]:  # the last piece may still be being written
        fields = line.split(",")
        if len(fields) == 9 and fields[2] == tag:
            runs.append((float(fields[0]), fields[3], int(fields[6]), int(fields[7]), int(fields[8])))
    return runs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="triwalk workspace with work/ and logs/")
    parser.add_argument("--tag", default="fresh1")
    parser.add_argument("--ids", type=Path, help="id list of the pass (default: work/changed_ids.txt)")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    ids = args.ids or args.workspace / "work" / "changed_ids.txt"
    total = sum(1 for line in ids.read_text().splitlines() if line.strip())
    log = args.workspace / "logs" / f"{args.tag}.log"
    shown = gain = 0
    print(f"passe {args.tag}: {total} puzzles. Ctrl+C sai daqui sem parar a busca.", flush=True)
    try:
        while True:
            summary = log.read_text().strip() if log.is_file() else ""
            runs = finished_runs(args.workspace / "work" / "tri_stats.csv", args.tag)
            for index in range(shown, len(runs)):
                stamp, puzzle, before, after, best = runs[index]
                saved = max(0, best - after)
                gain += saved
                elapsed = stamp - runs[0][0]
                left = f"faltam ~{(total - index - 1) * elapsed / index / 60:.0f} min" if index >= 8 else "estimando"
                result = f"{best} -> {after}  GANHOU {saved}" if saved else f"{best}  sem ganho"
                print(f"{time.strftime('%H:%M:%S', time.localtime(stamp))}  {index + 1:>3}/{total}  {puzzle}  "
                      f"{result:<24} | ganho total {gain} | {left}", flush=True)
            shown = len(runs)
            if summary:
                print(f"\nfim do passe:\n{summary}", flush=True)
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
