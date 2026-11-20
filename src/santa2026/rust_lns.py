"""Replay-validated Rust value-graph large-neighborhood search."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .core import Puzzle, validate_program
from .native_output import read_program_output


def binary_path():
    configured = os.environ.get("SANTA2026_LNS_BINARY")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2] / "outputs/bin/lns-rust"


def optimize_rust_lns(puzzle: Puzzle, program, seed=0, effort=1):
    """Search a continuous trajectory and keep a validated, nonregressing result.

    Feature bits: helper-root neighborhoods=1, equal-cost moves=2, depth-five
    near-miss products=4, bounded perturbations=8. The native engine keeps its
    best program separately from the potentially longer current trajectory.
    """
    validate_program(puzzle, program)
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    if puzzle.registers != 48:
        raise ValueError("The native LNS requires 48 registers")
    milliseconds = int(os.environ.get("SANTA2026_LNS_MS", str(min(120_000, 15_000 * effort))))
    flags = int(os.environ.get("SANTA2026_LNS_FLAGS", "15"))
    if not 1 <= milliseconds <= 600_000 or not 0 <= flags <= 15:
        raise ValueError("LNS requires 1..600000 milliseconds and feature bits 0..15")
    fields = ["puzzle", puzzle.value_limit, len(puzzle.starting_values), len(puzzle.targets),
              *puzzle.starting_values, *puzzle.targets, len(program)]
    for instruction in program:
        fields.extend(instruction)
    try:
        response = subprocess.run([str(binary_path()), str(seed % 2**64), str(milliseconds), str(flags)],
            input=" ".join(map(str, fields)) + "\n", text=True, capture_output=True,
            check=True, timeout=milliseconds / 1000 + 10)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
        candidate = None
        try:
            candidate = read_program_output(error.stdout, puzzle, require_complete=True)
        except ValueError:
            pass
        if candidate is None:
            raise
    else:
        candidate = read_program_output(response.stdout, puzzle)
        if candidate is None:
            raise ValueError("Missing LNS response")
    return candidate if len(candidate) < len(program) else [instruction[:] for instruction in program]
