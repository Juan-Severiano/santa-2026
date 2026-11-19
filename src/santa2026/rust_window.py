"""Validated bounded optimization of short native register windows."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .core import Puzzle, validate_program
from .native_output import read_program_output


def binary_path():
    configured = os.environ.get("SANTA2026_WINDOW_BINARY")
    if configured:
        return Path(configured).resolve()
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            return directory / "outputs/bin/window-rust"
    raise ValueError("Set SANTA2026_WINDOW_BINARY to the compiled window engine")


def optimize_rust_window(puzzle: Puzzle, program, seed=0, effort=1):
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    validate_program(puzzle, program)
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    if puzzle.registers != 48:
        raise ValueError("The native window search requires 48 registers")
    milliseconds = int(os.environ.get("SANTA2026_WINDOW_MS", str(min(12_000, 2000 * effort))))
    if not 1 <= milliseconds <= 120_000:
        raise ValueError("window budget must be between one and 120000 milliseconds")
    rename = os.environ.get("SANTA2026_WINDOW_RENAME", "0")
    if rename not in ("0", "1"):
        raise ValueError("window rename option must be zero or one")
    fields = ["puzzle", puzzle.value_limit, len(puzzle.starting_values), len(puzzle.targets),
              *puzzle.starting_values, *puzzle.targets, len(program)]
    for instruction in program:
        fields.extend(instruction)
    try:
        response = subprocess.run(
            [str(binary_path()), str(seed % 2**64), str(min(256, 24 * effort)), "32", "96", str(milliseconds), rename],
            input=" ".join(map(str, fields)) + "\n", text=True, capture_output=True, check=True,
            timeout=5 + milliseconds / 1000,
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
        candidate = read_program_output(response.stdout, puzzle)
        if candidate is None:
            raise ValueError("Missing native window response")
    return candidate if len(candidate) < len(program) else [instruction[:] for instruction in program]
