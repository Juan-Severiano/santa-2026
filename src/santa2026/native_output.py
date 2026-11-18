"""Parse and replay one native solver response, including interrupted output."""

from __future__ import annotations

import json

from .core import Puzzle, validate_program


def read_program_output(stdout, puzzle: Puzzle, require_complete=False):
    """Return one validated ``puzzle JSON`` program, or None for no response.

    Normal completed-process output may omit its final newline. Recovery from
    an interrupted process must set ``require_complete``: only newline-ended
    response lines are considered, and a trailing partial line (including
    incomplete UTF-8 bytes) is ignored. Complete malformed output raises
    ValueError instead of being mistaken for a recoverable incumbent.
    """
    if stdout is None:
        return None
    if not isinstance(stdout, (str, bytes)):
        raise ValueError("native solver output must be text or bytes")
    if require_complete:
        newline = b"\n" if isinstance(stdout, bytes) else "\n"
        stdout = stdout[:stdout.rfind(newline) + 1]
    if isinstance(stdout, bytes):
        try:
            stdout = stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("native solver output is not valid UTF-8") from exc
    response = stdout.strip()
    if not response:
        return None
    if require_complete:
        lines = [line.strip() for line in response.splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError("expected exactly one complete native solver response")
        response = lines[0]
    pieces = response.split(" ", 1)
    if len(pieces) != 2 or pieces[0] != "puzzle":
        raise ValueError("Unexpected native solver response")
    try:
        program = json.loads(pieces[1])
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid JSON in native solver response") from exc
    validate_program(puzzle, program)
    return program
