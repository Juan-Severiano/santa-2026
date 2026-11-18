"""Exact local replay and CSV handling for Santa 2026.

This implements the published calculator rules, rather than approximating them
with floating-point arithmetic. No Kaggle connection is needed for validation.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


MAX_INSTRUCTIONS = 4096
MAX_PROGRAM_BYTES = 131072
MAX_REGISTERS = 48
MAX_VALUE = 10**18


def _positive_integer(value: object, label: str, limit: int) -> None:
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError(f"{label} must be a positive integer no greater than {limit}")


@dataclass(frozen=True)
class Puzzle:
    id: str
    starting_values: tuple[int, ...]
    targets: tuple[int, ...]
    value_limit: int
    registers: int = MAX_REGISTERS

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("puzzle id must be a nonempty string")
        _positive_integer(self.value_limit, "value_limit", MAX_VALUE)
        _positive_integer(self.registers, "registers", MAX_REGISTERS)
        if not isinstance(self.starting_values, tuple) or not self.starting_values:
            raise ValueError("starting_values must be a nonempty tuple")
        if len(self.starting_values) > self.registers:
            raise ValueError("starting_values exceed the available registers")
        if not isinstance(self.targets, tuple):
            raise ValueError("targets must be a tuple")
        for value in self.starting_values:
            _positive_integer(value, "starting value", self.value_limit)
        for value in self.targets:
            _positive_integer(value, "target", self.value_limit)


@dataclass(frozen=True)
class ValidationResult:
    instructions: int
    covered_targets: int


def _program_json(program: object) -> str:
    try:
        return json.dumps(program, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("program must be JSON serializable") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_json(text: str, label: str) -> object:
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError(f"invalid JSON in {label}: {exc}") from exc


def _validate_structure(program: object) -> None:
    if type(program) is not list:
        raise ValueError("program must be a JSON array")
    if len(program) > MAX_INSTRUCTIONS:
        raise ValueError(f"program exceeds {MAX_INSTRUCTIONS} instructions")
    for number, instruction in enumerate(program, 1):
        if type(instruction) is not list or len(instruction) != 4:
            raise ValueError(f"instruction {number} must be a four-element JSON array")
        op, left, right, destination = instruction
        if type(op) is not str or op not in ("+", "-", "*", "/"):
            raise ValueError(f"instruction {number} has an invalid operator")
        for index in (left, right, destination):
            if type(index) is not int or not 0 <= index < MAX_REGISTERS:
                raise ValueError(f"instruction {number} has an invalid register index")
    if len(_program_json(program).encode("utf-8")) > MAX_PROGRAM_BYTES:
        raise ValueError(f"program exceeds {MAX_PROGRAM_BYTES} UTF-8 bytes")


def validate_program(puzzle: Puzzle, program: list[list]) -> ValidationResult:
    """Replay every instruction and reject any illegal or incomplete program."""
    if not isinstance(puzzle, Puzzle):
        raise ValueError("puzzle must be a Puzzle")
    _validate_structure(program)
    registers: list[int | None] = [None] * puzzle.registers
    registers[: len(puzzle.starting_values)] = puzzle.starting_values
    remaining = set(puzzle.targets).difference(puzzle.starting_values)
    for number, (op, left, right, destination) in enumerate(program, 1):
        if max(left, right, destination) >= puzzle.registers:
            raise ValueError(f"{puzzle.id}: instruction {number} exceeds available registers")
        first, second = registers[left], registers[right]
        if first is None or second is None:
            raise ValueError(f"{puzzle.id}: instruction {number} reads an undefined register")
        if op == "+":
            result = first + second
        elif op == "-":
            result = first - second
        elif op == "*":
            result = first * second
        else:
            result, remainder = divmod(first, second)
            if remainder:
                raise ValueError(f"{puzzle.id}: instruction {number} has inexact division")
        if not 1 <= result <= puzzle.value_limit:
            raise ValueError(
                f"{puzzle.id}: instruction {number} produces a value outside 1..{puzzle.value_limit}"
            )
        registers[destination] = result
        remaining.discard(result)
    if remaining:
        raise ValueError(f"{puzzle.id}: program misses {len(remaining)} target(s)")
    return ValidationResult(instructions=len(program), covered_targets=len(set(puzzle.targets)))


def read_puzzles(path: str | Path) -> list[Puzzle]:
    """Read either official puzzles CSV; practice metadata columns are ignored."""
    puzzles: list[Puzzle] = []
    seen: set[str] = set()
    required = {"id", "starting_values", "targets", "value_limit", "registers"}
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("puzzles CSV has missing or duplicate column headers")
        if not required.issubset(reader.fieldnames):
            raise ValueError(f"puzzles CSV requires columns: {', '.join(sorted(required))}")
        try:
            for row_number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"puzzles CSV row {row_number} has the wrong number of cells")
                starting = _parse_json(row["starting_values"], "starting_values")
                targets = _parse_json(row["targets"], "targets")
                if type(starting) is not list or type(targets) is not list:
                    raise ValueError(f"puzzles CSV row {row_number} requires JSON arrays")
                try:
                    value_limit, registers = int(row["value_limit"]), int(row["registers"])
                except ValueError as exc:
                    raise ValueError(f"puzzles CSV row {row_number} has an invalid integer") from exc
                puzzle = Puzzle(row["id"], tuple(starting), tuple(targets), value_limit, registers)
                if puzzle.id in seen:
                    raise ValueError(f"duplicate puzzle id: {puzzle.id}")
                seen.add(puzzle.id)
                puzzles.append(puzzle)
        except csv.Error as exc:
            raise ValueError(f"invalid puzzles CSV: {exc}") from exc
    return puzzles


def read_submission(path: str | Path) -> dict[str, list[list]]:
    """Load a strict two-column CSV, preserving unique IDs and JSON arrays."""
    programs: dict[str, list[list]] = {}
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        try:
            if next(reader, None) != ["id", "program"]:
                raise ValueError("submission CSV must have exactly the columns id,program in that order")
            for row_number, row in enumerate(reader, 2):
                if len(row) != 2 or not row[0]:
                    raise ValueError(f"submission CSV row {row_number} requires a nonempty id and program")
                identifier, text = row
                if identifier in programs:
                    raise ValueError(f"duplicate submission id: {identifier}")
                if len(text.encode("utf-8")) > MAX_PROGRAM_BYTES:
                    raise ValueError(f"{identifier}: program exceeds {MAX_PROGRAM_BYTES} UTF-8 bytes")
                program = _parse_json(text, f"program for {identifier}")
                _validate_structure(program)
                programs[identifier] = program
        except csv.Error as exc:
            raise ValueError(f"invalid submission CSV: {exc}") from exc
    return programs


def write_submission(path: str | Path, programs: dict[str, list[list]]) -> None:
    """Write compact JSON cells with correct CSV escaping."""
    if not isinstance(programs, Mapping):
        raise ValueError("programs must be a mapping from IDs to programs")
    rows = []
    for identifier, program in programs.items():
        if type(identifier) is not str or not identifier:
            raise ValueError("submission id must be a nonempty string")
        _validate_structure(program)
        rows.append((identifier, _program_json(program)))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "program"])
        writer.writerows(rows)


def score_submission(
    puzzles: Sequence[Puzzle], programs: Mapping[str, list[list]]
) -> dict[str, ValidationResult]:
    """Require precisely the supplied puzzle IDs, then replay every program."""
    puzzle_ids = [puzzle.id for puzzle in puzzles]
    if len(set(puzzle_ids)) != len(puzzle_ids):
        raise ValueError("duplicate puzzle IDs")
    missing = set(puzzle_ids).difference(programs)
    extra = set(programs).difference(puzzle_ids)
    if missing or extra:
        raise ValueError(f"submission ID mismatch: {len(missing)} missing, {len(extra)} extra")
    return {puzzle.id: validate_program(puzzle, programs[puzzle.id]) for puzzle in puzzles}
