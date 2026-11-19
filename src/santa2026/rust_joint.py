"""Validated simultaneous recipe-graph optimization using a native Rust engine."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .core import Puzzle, validate_program
from .native_output import read_program_output


def binary_path():
    configured = os.environ.get("SANTA2026_JOINT_BINARY")
    if configured:
        return Path(configured).resolve()
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            return directory / "outputs/bin/joint-rust"
    raise ValueError("Set SANTA2026_JOINT_BINARY to the compiled joint search engine")


def optimize_rust_joint(puzzle: Puzzle, programs, seed=0, effort=1):
    """Keep the shortest valid hint unless coordinated graph changes improve it."""
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    if not isinstance(programs, (list, tuple)) or not 1 <= len(programs) <= 64:
        raise ValueError("one to 64 candidate programs are required")
    for program in programs:
        validate_program(puzzle, program)
    original = min(programs, key=len)
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    if puzzle.registers != 48:
        raise ValueError("The native joint search requires 48 registers")
    ordered = sorted(programs, key=len)
    fields = ["puzzle", puzzle.value_limit, len(puzzle.starting_values), len(puzzle.targets),
              *puzzle.starting_values, *puzzle.targets, len(ordered)]
    for program in ordered:
        fields.append(len(program))
        for instruction in program:
            fields.extend(instruction)
    budget = int(os.environ.get("SANTA2026_JOINT_MS", str(min(12_000, 2000 * effort))))
    if not 1 <= budget <= 120_000:
        raise ValueError("joint time budget must be between one and 120000 milliseconds")
    helpers = int(os.environ.get("SANTA2026_JOINT_HELPERS", "0"))
    if not 0 <= helpers <= 24:
        raise ValueError("joint helper budget must be between zero and 24")
    plans = int(os.environ.get("SANTA2026_JOINT_PLANS", "0"))
    if not 0 <= plans <= 256:
        raise ValueError("joint plan budget must be between zero and 256")
    try:
        completed = subprocess.run(
            [str(binary_path()), str(seed % 2**64), str(min(40_000, 1600 * effort)), "8", "1024", str(budget), str(helpers), str(plans)],
            input=" ".join(map(str, fields)) + "\n", text=True, capture_output=True, check=True,
            timeout=30 + budget / 1000,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
        candidate = None
        try:
            candidate = read_program_output(error.stdout, puzzle, require_complete=True)
        except ValueError:
            pass
        if candidate is None:
            raise
    else:
        candidate = read_program_output(completed.stdout, puzzle)
        if candidate is None:
            raise ValueError("Missing joint search response")
    return candidate if len(candidate) < len(original) else [instruction[:] for instruction in original]
