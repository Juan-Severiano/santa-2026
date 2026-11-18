"""Exact compiled short-recipe search within an existing arithmetic graph."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from .core import validate_program


def binary_path():
    configured = os.environ.get("SANTA2026_MITM_BINARY")
    if configured:
        return Path(configured).resolve()
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            return directory / "outputs" / "bin" / "mitm"
    raise ValueError("Set SANTA2026_MITM_BINARY to the compiled exact-search engine")


def optimize_mitm(puzzle, program, seed=0, effort=1):
    validate_program(puzzle, program)
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    if puzzle.registers != 48:
        raise ValueError("The compiled exact-search engine requires 48 registers")
    leaves = (32, 48, 56, 40)[seed % 4]
    trials = min(64, int(os.environ.get("SANTA2026_REPAIR_TRIALS", "8")) * max(1, effort))
    depth = (2, 3, 4)[(seed // 4) % 3]
    line = " ".join(map(str, ("puzzle", puzzle.value_limit, len(puzzle.starting_values), len(puzzle.targets),
                             *puzzle.starting_values, *sorted(puzzle.targets)))) + "\n"
    line += str(len(program)) + "\n" + "\n".join(" ".join(map(str, item)) for item in program) + "\n"
    result = subprocess.run([str(binary_path()), str(leaves), str(trials), str(seed), str(depth)],
                            input=line, text=True, capture_output=True, check=True,
                            timeout=120 * max(1, effort))
    identifier, cell = result.stdout.strip().split(" ", 1)
    if identifier != "puzzle":
        raise ValueError("Unexpected exact-search response")
    candidate = json.loads(cell)
    validate_program(puzzle, candidate)
    return candidate if len(candidate) < len(program) else [instruction[:] for instruction in program]
