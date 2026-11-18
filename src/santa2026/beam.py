"""Validated interface to the public C++ dictionary/beam research baseline.

The original solver is by Tony Li; the parameterized portfolio is by konbu17:
https://www.kaggle.com/code/tonylica/elf-calculator-with-register-reuse
https://www.kaggle.com/code/konbu17/santa-2026-wider-dictionary-portfolio-163k

Downloaded third-party sources and binaries stay in the ignored data directory.
This module never downloads or compiles anything implicitly.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .core import Puzzle, validate_program


def binary_path():
    configured = os.environ.get("SANTA2026_BEAM_BINARY")
    if configured:
        return Path(configured).resolve()
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            compiled = directory / "outputs" / "bin" / "beam"
            if compiled.is_file():
                return compiled
            return directory / "data" / "public" / "konbu17" / "solver"
    raise ValueError("Set SANTA2026_BEAM_BINARY to the prepared C++ solver")


def _settings(seed, effort):
    settings = (
        (3, .18, 52, 56, 20),
        (10, .18, 72, 88, 22),
        (30, .18, 64, 96, 22),
        (10, .18, 64, 72, 22),
        (10, .12, 72, 88, 22),
        (10, .25, 72, 88, 22),
        (30, .18, 80, 112, 23),
        (3, .18, 40, 48, 20),
    )
    width, alpha, done, recent, bits = settings[seed % len(settings)]
    width = min(100, width * max(1, effort))
    return width, alpha, done, recent, bits, seed // len(settings)


def _invoke(puzzle, seed, effort, mode, hint=None):
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    if puzzle.registers != 48:
        raise ValueError("The C++ engine requires exactly 48 registers")
    width, alpha, done, recent, bits, schedule_seed = _settings(seed, effort)
    # A fixed opaque identifier keeps the line protocol independent of CSV IDs.
    line = " ".join(map(str, ("puzzle", puzzle.value_limit, len(puzzle.starting_values),
                             len(puzzle.targets), *puzzle.starting_values, *sorted(puzzle.targets)))) + "\n"
    if hint is not None:
        line += str(len(hint)) + "\n"
        line += "\n".join(" ".join(map(str, instruction)) for instruction in hint) + "\n"
    completed = subprocess.run(
        [str(binary_path()), str(width), str(alpha), str(done), str(recent), str(bits),
         str(mode), str(schedule_seed)],
        input=line, text=True, capture_output=True, timeout=120 * max(1, effort), check=True,
    )
    identifier, cell = completed.stdout.strip().split(" ", 1)
    if identifier != "puzzle":
        raise ValueError("Unexpected C++ solver response")
    program = json.loads(cell)
    validate_program(puzzle, program)
    return program


def solve_beam(puzzle: Puzzle, seed: int = 0, effort: int = 1):
    """Generate one parameter setting; the lab handles per-puzzle fallback."""
    return _invoke(puzzle, seed, effort, int(os.environ.get("SANTA2026_BEAM_MODE", "0")))


def polish_beam(puzzle: Puzzle, program, seed: int = 0, effort: int = 1):
    """Repair an existing program using its arithmetic graph as search context."""
    validate_program(puzzle, program)
    candidate = _invoke(puzzle, seed, effort, 6, hint=program)
    return candidate if len(candidate) < len(program) else [instruction[:] for instruction in program]
