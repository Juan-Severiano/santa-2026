"""Bounded backward arithmetic synthesis using the puzzle's free seed values.

This complements positional constructors: each target can choose a different
live multiplier and a separately synthesized positive or negative remainder.
Plans are finite expression DAGs; emission checks every calculator operation
and keeps live dependencies pinned while reusing the 48 physical registers.
"""

from __future__ import annotations

import random
from typing import NamedTuple

from .core import Puzzle, validate_program
from .solver import _Machine, _window_terms, solve


class _Expression(NamedTuple):
    value: int
    cost: int = 0
    op: str | None = None
    left: _Expression | None = None
    right: _Expression | None = None


class _Planner:
    def __init__(self, machine: _Machine, initial: tuple[int, ...], effort: int):
        self.known = set(machine.locations)
        self.leaves = {value: _Expression(value) for value in self.known}
        self.limit = machine.limit
        self.effort = effort
        self.basic_cache: dict[int, _Expression] = {}
        self.search_cache: dict[tuple[int, int], _Expression] = {}
        self.bases = {value: self.leaves[value] for value in self.known if value > 1}
        # A product of free initial values costs just one instruction. These
        # larger bases can remove many bits in one multiplication of a quotient.
        for index, left in enumerate(initial):
            for right in initial[index:]:
                value = left * right
                if 1 < value <= self.limit and value not in self.bases:
                    self.bases[value] = self.node("*", self.leaves[left], self.leaves[right])
        # Cheap non-power-of-two multipliers offer different residuals from
        # shifts alone. They are synthesized only if the selected plan uses one.
        for exponent in range(5, 11):
            power = 1 << exponent
            if power not in self.known:
                continue
            for operation in ("+", "-"):
                value = power + (1 if operation == "+" else -1)
                if 1 < value <= self.limit and value not in self.bases:
                    self.bases[value] = self.node(operation, self.leaves[power], self.leaves[1])

    def node(self, operation: str, left: _Expression, right: _Expression) -> _Expression:
        if operation == "+":
            value = left.value + right.value
        elif operation == "-":
            value = left.value - right.value
        elif operation == "*":
            value = left.value * right.value
        else:
            if left.value % right.value:
                raise ValueError("Synthesis division must be exact")
            value = left.value // right.value
        if not 1 <= value <= self.limit:
            raise ValueError("Synthesis intermediate is outside value_limit")
        if value in self.known:
            return self.leaves[value]
        cost = left.cost + (0 if left.value == right.value else right.cost) + 1
        return _Expression(value, cost, operation, left, right)

    def direct(self, target: int) -> _Expression | None:
        for left in self.known:
            for operation, right, reverse in (
                ("+", target - left, False),
                ("-", left - target, False),
                ("-", target + left, True),
            ):
                if right > 0 and right in self.known:
                    a, b = (self.leaves[right], self.leaves[left]) if reverse else (self.leaves[left], self.leaves[right])
                    return self.node(operation, a, b)
            if target % left == 0 and target // left in self.known:
                return self.node("*", self.leaves[left], self.leaves[target // left])
            if left % target == 0 and left // target in self.known:
                return self.node("/", self.leaves[left], self.leaves[left // target])
            if target * left in self.known:
                return self.node("/", self.leaves[target * left], self.leaves[left])
        return None

    def basic(self, value: int) -> _Expression:
        if value in self.known:
            return self.leaves[value]
        if value in self.basic_cache:
            return self.basic_cache[value]
        direct = self.direct(value)
        if direct is not None:
            self.basic_cache[value] = direct
            return direct
        candidates = []
        for width in (4, 5, 6):
            try:
                terms = _window_terms(value, width)
                expression = self.small(terms[0][1])
                for shift, digit in terms[1:]:
                    while shift:
                        step = min(10, shift)
                        expression = self.node("*", expression, self.small(1 << step))
                        shift -= step
                    if digit:
                        expression = self.node("+" if digit > 0 else "-", expression, self.small(abs(digit)))
                candidates.append(expression)
            except ValueError:
                continue
        if not candidates:
            # Unsigned binary never overshoots the positive target.
            expression = self.small(1)
            for bit in bin(value)[3:]:
                expression = self.node("+", expression, expression)
                if bit == "1":
                    expression = self.node("+", expression, self.small(1))
            candidates.append(expression)
        result = min(candidates, key=lambda expression: expression.cost)
        self.basic_cache[value] = result
        return result

    def small(self, value: int) -> _Expression:
        if value in self.known:
            return self.leaves[value]
        if not 1 <= value <= self.limit:
            raise ValueError("Synthesis constant is outside value_limit")
        # One is always prepared before the planner is used.
        if value == 1:
            raise ValueError("Synthesis requires a defined unit constant")
        half = self.small(value // 2)
        expression = self.node("+", half, half)
        if value % 2:
            expression = self.node("+", expression, self.leaves[1])
        return expression

    def search(self, value: int, depth: int) -> _Expression:
        cache_key = value, depth
        if cache_key in self.search_cache:
            return self.search_cache[cache_key]
        best = self.basic(value)
        if depth <= 0 or best.cost <= 1:
            return best
        refinements = []
        for multiplier, multiplier_plan in self.bases.items():
            if multiplier >= value:
                difference = multiplier - value
                if difference:
                    remainder = self.basic(difference)
                    candidate = self.node("-", multiplier_plan, remainder)
                    if candidate.cost < best.cost:
                        best = candidate
                if multiplier % value == 0:
                    candidate = self.node("/", multiplier_plan, self.basic(multiplier // value))
                    if candidate.cost < best.cost:
                        best = candidate
                continue
            quotient, remainder = divmod(value, multiplier)
            quotient_plan = self.basic(quotient)
            if quotient_plan.cost + multiplier_plan.cost + 1 > best.cost + depth:
                continue
            product = self.node("*", quotient_plan, multiplier_plan)
            candidate = self.node("+", product, self.basic(remainder)) if remainder else product
            refinements.append((candidate.cost, quotient, remainder, False, multiplier_plan))
            if candidate.cost < best.cost:
                best = candidate
            # A balanced residue can be far cheaper than the positive residue.
            if remainder and (quotient + 1) * multiplier <= self.limit:
                ceil_quotient = quotient + 1
                residue_plan = self.basic(multiplier - remainder)
                product = self.node("*", self.basic(ceil_quotient), multiplier_plan)
                candidate = self.node("-", product, residue_plan)
                refinements.append((candidate.cost, ceil_quotient, multiplier - remainder, True, multiplier_plan))
                if candidate.cost < best.cost:
                    best = candidate
        if depth > 1:
            # Only the most promising quotient branches receive more search;
            # effort bounds the branching independently of target magnitude.
            refinements.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4].value))
            for _, quotient, remainder, negative, multiplier_plan in refinements[: min(4, self.effort + 1)]:
                product = self.node("*", self.search(quotient, depth - 1), multiplier_plan)
                candidate = self.node("-" if negative else "+", product, self.basic(remainder)) if remainder else product
                if candidate.cost < best.cost:
                    best = candidate
        self.search_cache[cache_key] = best
        return best


def _synthesize(puzzle: Puzzle, seed: int, effort: int) -> list[list]:
    maximum = min(puzzle.value_limit, max(puzzle.targets, default=1))
    protected = set(puzzle.starting_values) | {1}
    protected.update(value for value in range(3, 32, 2) if value <= maximum)
    protected.update(1 << exponent for exponent in range(1, 11) if 1 << exponent <= maximum)
    machine = _Machine(puzzle, protected)

    def constant(value: int, pinned=frozenset()) -> int:
        register = machine.register(value)
        if register is not None:
            return register
        direct = machine.direct(value)
        if direct is not None:
            return machine.emit(*direct, pinned=pinned)
        if value == 1:
            source = next(index for index, item in enumerate(machine.values) if item is not None)
            return machine.emit("/", source, source, pinned=pinned)
        half = constant(value // 2, pinned)
        doubled = machine.emit("+", half, half, pinned=pinned)
        if value % 2:
            one = constant(1, pinned | frozenset({doubled}))
            return machine.emit("+", doubled, one, pinned=pinned)
        return doubled

    progress = True
    while progress and machine.remaining:
        progress = False
        for target in sorted(machine.remaining):
            if target not in machine.remaining:
                continue
            direct = machine.direct(target)
            if direct:
                machine.emit(*direct)
                progress = True
    if not machine.remaining:
        return machine.program[:machine.completed_at]
    for value in sorted(protected - set(puzzle.starting_values)):
        constant(value)
        if not machine.remaining:
            return machine.program[:machine.completed_at]

    targets = sorted(machine.remaining)
    if seed % 4 == 1:
        targets.reverse()
    elif seed % 4 == 2:
        random.Random(seed).shuffle(targets)
    elif seed % 4 == 3:
        # Alternate magnitude extremes to expose factors and useful residuals.
        targets = targets[::2] + list(reversed(targets[1::2]))

    def emit(expression: _Expression, pinned=frozenset()) -> int:
        register = machine.register(expression.value)
        if register is not None:
            return register
        direct = machine.direct(expression.value)
        if direct:
            return machine.emit(*direct, pinned=pinned)
        if expression.op is None:
            # An unprotected cached leaf may have been recycled since planning.
            # Reconstruct it from the protected constants and original seeds.
            planner = _Planner(machine, puzzle.starting_values, 1)
            replacement = planner.basic(expression.value)
            if replacement.op is None:
                raise ValueError("Synthesis lost an unavailable leaf")
            return emit(replacement, pinned)
        left = emit(expression.left, pinned)
        right = emit(expression.right, pinned | frozenset({left}))
        return machine.emit(expression.op, left, right, pinned=pinned)

    for target in targets:
        if target not in machine.remaining:
            continue
        planner = _Planner(machine, puzzle.starting_values, effort)
        emit(planner.search(target, min(effort, 3)))
        if not machine.remaining:
            break
    if machine.remaining:
        raise ValueError("Synthesis did not cover every target")
    return machine.program[:machine.completed_at]


def solve_synthesis(puzzle: Puzzle, seed: int = 0, effort: int = 1) -> list[list]:
    """Return a deterministic valid candidate; effort bounds backward search.

    Different seeds choose target schedules. The signed-window constructor is
    retained as a per-puzzle fallback, so this strategy never worsens it.
    """
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    fallback = solve(puzzle, strategy="window")
    try:
        candidate = _synthesize(puzzle, seed, min(effort, 4))
        validate_program(puzzle, candidate)
    except ValueError:
        return fallback
    return candidate if len(candidate) < len(fallback) else fallback
