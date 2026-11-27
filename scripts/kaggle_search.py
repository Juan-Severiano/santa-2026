#!/usr/bin/env python3
"""Prepare the private Kaggle search job locally: one dataset folder and one folder per kernel.

This script uploads and runs nothing. It prints the commands that do.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from santa2026.core import read_puzzles, read_submission, score_submission

SLUG = "santa-2026-search-state"
GOODPJW_FILES = ("triwalk.cpp", "elf.py", "pfmt.py", "tri_driver.py", "gpu_lns.py")
ROSSWADE_FILES = ("lns_gpu.py", "gpu_mitm2.py")
NOTICE = """# Sources used by this private search job

- triwalk.cpp, elf.py, pfmt.py, tri_driver.py, gpu_lns.py: goodpjw2008, Kaggle dataset
  goodpjw2008/santa-2026-dag-lns-programs (Apache-2.0). Unmodified.
- lns_gpu.py, gpu_mitm2.py: Ross Wade, Kaggle dataset rosswade/santa-2026-elf-calculator-proved (CC0-1.0). Unmodified.
- best.csv and lineages.jsonl: per-puzzle programs replayed from public Santa 2026 program banks by many
  authors; they are search starting points and are not this account's own constructions.
"""


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Kaggle user name")
    parser.add_argument("--best", type=Path, default=ROOT / "outputs" / "best.csv")
    parser.add_argument("--lineages", type=Path, required=True)
    parser.add_argument("--goodpjw", type=Path, required=True, help="downloaded goodpjw2008 dataset folder")
    parser.add_argument("--rosswade", type=Path, required=True, help="downloaded rosswade dataset folder")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "kaggle-search")
    parser.add_argument("--cpu-kernels", type=int, default=4)
    parser.add_argument("--cpu-hours", type=float, default=11.0)
    parser.add_argument("--gpu-hours", type=float, default=9.0, help="0 disables the GPU kernel")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9]+", args.owner):
        parser.error("--owner must be a Kaggle user name")
    if args.cpu_hours > 11.5 or args.gpu_hours > 11.5:
        parser.error("keep sessions under 11.5 h so Kaggle saves the output before its 12 h limit")

    puzzles = read_puzzles(ROOT / "data" / "puzzles.csv")
    results = score_submission(puzzles, read_submission(args.best))
    score = sum(result.instructions for result in results.values())

    dataset = args.out / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    for name in GOODPJW_FILES:
        shutil.copyfile(args.goodpjw / name, dataset / name)
    for name in ROSSWADE_FILES:
        shutil.copyfile(args.rosswade / name, dataset / name)
    shutil.copyfile(args.best, dataset / "best.csv")
    shutil.copyfile(args.lineages, dataset / "lineages.jsonl")
    shutil.copyfile(ROOT / "licenses" / "Apache-2.0.txt", dataset / "Apache-2.0.txt")
    (dataset / "NOTICE.md").write_text(NOTICE)
    write_json(dataset / "dataset-metadata.json",
               {"title": SLUG, "id": f"{args.owner}/{SLUG}", "licenses": [{"name": "other"}]})

    kernels = [(f"santa-search-cpu-{index}", False, args.cpu_hours) for index in range(args.cpu_kernels)]
    if args.gpu_hours > 0:
        kernels.append(("santa-search-gpu", True, args.gpu_hours))
    template = (ROOT / "scripts" / "kaggle_runner.py").read_text()
    default = 'CONFIG = {"shard": 0, "shards": 1, "hours": 11.0, "gpu": False, "reserve": 240}'
    if default not in template:
        raise ValueError("kaggle_runner.py CONFIG line changed; update this builder")
    folders = []
    for shard, (name, gpu, hours) in enumerate(kernels):
        folder = args.out / name
        folder.mkdir(parents=True, exist_ok=True)
        config = {"shard": shard, "shards": len(kernels), "hours": hours, "gpu": gpu, "reserve": 240}
        (folder / "runner.py").write_text(template.replace(default, f"CONFIG = {config!r}"))
        write_json(folder / "kernel-metadata.json", {
            "id": f"{args.owner}/{name}", "title": name, "code_file": "runner.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": gpu, "enable_tpu": False,
            "enable_internet": False, "competition_sources": ["santa-2026"],
            "dataset_sources": [f"{args.owner}/{SLUG}"], "kernel_sources": [], "model_sources": []})
        folders.append((folder, gpu))

    print(f"starting score {score}; dataset at {dataset}")
    print(f"kaggle datasets create -p {dataset} -t")
    for folder, gpu in folders:
        print(f"kaggle kernels push -p {folder}" + (" --accelerator NvidiaTeslaT4" if gpu else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
