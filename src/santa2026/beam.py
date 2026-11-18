"""Validated native search interface; mode-6 repair uses Rust by default.

The original solver is by Tony Li; the parameterized portfolio is by konbu17:
https://www.kaggle.com/code/tonylica/elf-calculator-with-register-reuse
https://www.kaggle.com/code/konbu17/santa-2026-wider-dictionary-portfolio-163k

Downloaded third-party sources and binaries stay in the ignored data directory.
This module never downloads or compiles anything implicitly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .core import Puzzle, validate_program
from .native_output import read_program_output


DEFAULT_REPAIR_ENGINE = "rust"


def binary_path(repair=False):
    configured = os.environ.get("SANTA2026_REPAIR_BINARY") if repair else None
    configured = configured or os.environ.get("SANTA2026_BEAM_BINARY")
    if configured:
        return Path(configured).resolve()
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").is_file():
            if repair:
                return directory / "outputs" / "bin" / ("repair-" + DEFAULT_REPAIR_ENGINE)
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
        raise ValueError("The native search engine requires exactly 48 registers")
    width, alpha, done, recent, bits, schedule_seed = _settings(seed, effort)
    # A fixed opaque identifier keeps the line protocol independent of CSV IDs.
    line = " ".join(map(str, ("puzzle", puzzle.value_limit, len(puzzle.starting_values),
                             len(puzzle.targets), *puzzle.starting_values, *sorted(puzzle.targets)))) + "\n"
    if hint is not None:
        line += str(len(hint)) + "\n"
        line += "\n".join(" ".join(map(str, instruction)) for instruction in hint) + "\n"
    command = [str(binary_path(repair=(mode == 6))), str(width), str(alpha), str(done), str(recent), str(bits),
               str(mode), str(schedule_seed)]
    if hint is not None:
        command += [os.environ.get("SANTA2026_REPAIR_TRIALS", "12"), os.environ.get("SANTA2026_REPAIR_PASSES", "1")]
    try:
        completed = subprocess.run(
            command,
            input=line, text=True, capture_output=True, timeout=120 * max(1, effort), check=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
        recovered = None
        try:
            recovered = read_program_output(error.stdout, puzzle, require_complete=True)
        except (ValueError, TypeError, RecursionError):
            pass
        if recovered is not None:
            return recovered
        raise
    program = read_program_output(completed.stdout, puzzle)
    if program is None:
        raise ValueError("Unexpected native solver response")
    return program


def solve_beam(puzzle: Puzzle, seed: int = 0, effort: int = 1):
    """Generate one parameter setting; the lab handles per-puzzle fallback."""
    return _invoke(puzzle, seed, effort, int(os.environ.get("SANTA2026_BEAM_MODE", "0")))


def polish_beam(puzzle: Puzzle, program, seed: int = 0, effort: int = 1):
    """Repair an existing program using its arithmetic graph as search context."""
    validate_program(puzzle, program)
    candidate = _invoke(puzzle, seed, effort, int(os.environ.get("SANTA2026_REPAIR_MODE", "6")), hint=program)
    return candidate if len(candidate) < len(program) else [instruction[:] for instruction in program]
