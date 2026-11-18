"""Deterministic arithmetic-program baseline for the Elf Calculator.

The portfolio builds numbers in several positional bases, retains useful small
constants, and reuses live values whenever one arithmetic operation suffices.
It is a constructive baseline, not an optimal-program search.
"""

from __future__ import annotations

from .core import Puzzle


class _Machine:
    def __init__(self, puzzle: Puzzle, protected: set[int]):
        self.limit = puzzle.value_limit
        self.values: list[int | None] = list(puzzle.starting_values) + [None] * (
            puzzle.registers - len(puzzle.starting_values)
        )
        self.locations: dict[int, set[int]] = {}
        for register, value in enumerate(puzzle.starting_values):
            self.locations.setdefault(value, set()).add(register)
        self.protected = protected
        self.ages = [0] * puzzle.registers
        self.program: list[list] = []
        self.remaining = set(puzzle.targets) - set(puzzle.starting_values)
        self.completed_at = 0 if not self.remaining else None

    def register(self, value: int) -> int | None:
        locations = self.locations.get(value)
        return min(locations) if locations else None

    def direct(self, target: int) -> tuple[str, int, int] | None:
        """Find one legal operation yielding target, without quadratic pairs."""
        for left in self.locations:
            right = target - left
            if right > 0 and right in self.locations:
                return "+", self.register(left), self.register(right)
            right = left - target
            if right > 0 and right in self.locations:
                return "-", self.register(left), self.register(right)
            right = target + left
            if right in self.locations:
                return "-", self.register(right), self.register(left)
            if target % left == 0 and target // left in self.locations:
                return "*", self.register(left), self.register(target // left)
            if left % target == 0 and left // target in self.locations:
                return "/", self.register(left), self.register(left // target)
            right = target * left
            if right in self.locations:
                return "/", self.register(right), self.register(left)
        return None

    def emit(self, operation: str, left: int, right: int, pinned=frozenset()) -> int:
        a, b = self.values[left], self.values[right]
        if operation == "+":
            result = a + b
        elif operation == "-":
            result = a - b
        elif operation == "*":
            result = a * b
        else:
            if a % b:
                raise ValueError("Baseline attempted an inexact division")
            result = a // b
        if not 1 <= result <= self.limit:
            raise ValueError("Baseline attempted an out-of-range result")
        existing = self.register(result)
        if existing is not None:
            return existing

        # Undefined and duplicate slots cost nothing to recycle. Keep one copy
        # of every protected constant; other values use a least-recently-used
        # cache. Sources are read before the destination is replaced.
        available = [i for i in range(len(self.values)) if i not in pinned]
        disposable = [
            i for i in available
            if self.values[i] is None
            or len(self.locations[self.values[i]]) > 1
        ]
        if disposable:
            destination = min(disposable)
        else:
            available = [i for i in available if self.values[i] not in self.protected]
            if not available:
                raise ValueError("Insufficient scratch registers for baseline")
            # The operands become recent now so older cached prefixes survive
            # only while they are actually useful.
            self.ages[left] = self.ages[right] = len(self.program) + 1
            destination = min(available, key=lambda i: (self.ages[i], i))

        old = self.values[destination]
        if old is not None:
            self.locations[old].remove(destination)
            if not self.locations[old]:
                del self.locations[old]
        self.values[destination] = result
        self.locations.setdefault(result, set()).add(destination)
        self.program.append([operation, left, right, destination])
        self.ages[left] = self.ages[right] = self.ages[destination] = len(self.program)
        self.remaining.discard(result)
        if not self.remaining and self.completed_at is None:
            self.completed_at = len(self.program)
        return destination


def _digits(value: int, base: int) -> list[int]:
    digits = []
    while value:
        value, digit = divmod(value, base)
        digits.append(digit)
    return digits


def _candidate_bases(puzzle: Puzzle) -> list[int]:
    missing = set(puzzle.targets) - set(puzzle.starting_values)
    maximum = min(puzzle.value_limit, max(missing, default=1), 32)
    ranked = []
    for base in range(2, maximum + 1):
        constants = {1, base}
        cost = 0
        for target in missing:
            digits = _digits(target, base)
            constants.update(digit for digit in digits if digit)
            cost += max(0, len(digits) - 1) + sum(bool(x) for x in digits[:-1])
        cost += len(constants - set(puzzle.starting_values))
        ranked.append((cost, base))
    selected = {base for _, base in sorted(ranked)[:4]}
    selected.update(base for base in (2, 3, 4, 5, 8, 10, 16, 32) if base <= maximum)
    return sorted(selected)


def _solve_base(puzzle: Puzzle, base: int) -> list[list]:
    missing = set(puzzle.targets) - set(puzzle.starting_values)
    constants = {1, base}
    for target in missing:
        constants.update(digit for digit in _digits(target, base) if digit)
    machine = _Machine(puzzle, set(range(1, base + 1)))

    def make_constant(value: int, pinned=frozenset()) -> int:
        register = machine.register(value)
        if register is not None:
            return register
        direct = machine.direct(value)
        if direct is not None:
            return machine.emit(*direct, pinned=pinned)
        if value == 1:
            register = next(i for i, value in enumerate(machine.values) if value is not None)
            return machine.emit("/", register, register, pinned=pinned)
        half = make_constant(value // 2, pinned)
        doubled = machine.emit("+", half, half, pinned=pinned)
        if value % 2:
            # One is protected and therefore cannot be evicted by doubling.
            return machine.emit("+", doubled, make_constant(1, pinned), pinned=pinned)
        return doubled

    # Cheap target closure can finish small/shared puzzles without paying for
    # an otherwise unused positional basis.
    progress = True
    while progress and machine.remaining:
        progress = False
        for target in sorted(machine.remaining):
            if target not in machine.remaining:
                continue
            direct = machine.direct(target)
            if direct is not None:
                machine.emit(*direct)
                progress = True
    if not machine.remaining:
        return machine.program[:machine.completed_at]

    for constant in sorted(constants):
        make_constant(constant)
        if not machine.remaining:
            return machine.program[:machine.completed_at]

    def build(value: int, pinned=frozenset()) -> int:
        register = machine.register(value)
        if register is not None:
            return register
        direct = machine.direct(value)
        if direct is not None:
            return machine.emit(*direct, pinned=pinned)
        quotient, remainder = divmod(value, base)
        # Every positive one-digit value needed by build was precomputed.
        # A difference/quotient introduced by reuse may have a new digit.
        if not quotient:
            return make_constant(value, pinned)
        source = build(quotient, pinned)
        product = machine.emit("*", source, machine.register(base), pinned=pinned)
        if remainder:
            digit = machine.register(remainder)
            if digit is None:
                # Pin the partial result while constructing a new small digit.
                digit = make_constant(remainder, pinned | frozenset({product}))
            return machine.emit("+", product, digit, pinned=pinned)
        return product

    def estimate(value: int) -> int:
        if value in machine.locations:
            return 0
        cost = 0
        while value >= base and value not in machine.locations:
            value, digit = divmod(value, base)
            cost += 1 + bool(digit)
        return cost + (0 if value in machine.locations else value.bit_length())

    for target in sorted(missing):
        if target not in machine.remaining:
            continue
        if machine.register(target) is not None:
            continue
        direct = machine.direct(target)
        if direct is not None:
            machine.emit(*direct)
            continue
        best = (estimate(target), "build", target, None)
        # Differences exploit clustered targets; factors exploit multiplicative
        # families. Restrict difference checks to nearby values for speed.
        values = sorted(machine.locations, key=lambda value: (abs(target - value), value))
        for anchor in values[:8]:
            difference = abs(target - anchor)
            if difference:
                candidate = (estimate(difference) + 1, "+" if target > anchor else "-", difference, anchor)
                if candidate[0] < best[0]:
                    best = candidate
        for anchor in values:
            if 1 < anchor < target and target % anchor == 0:
                candidate = (estimate(target // anchor) + 1, "*", target // anchor, anchor)
            elif anchor > target and anchor % target == 0:
                candidate = (estimate(anchor // target) + 1, "/", anchor // target, anchor)
            else:
                continue
            if candidate[0] < best[0]:
                best = candidate
        _, operation, argument, anchor = best
        if operation == "build":
            build(target)
        else:
            anchor_register = machine.register(anchor)
            argument_register = build(argument, frozenset({anchor_register}))
            machine.emit(operation, anchor_register, argument_register)
        if not machine.remaining:
            break
    if machine.remaining:
        raise ValueError("Baseline failed to cover all targets")
    return machine.program[:machine.completed_at]


def solve_radix(puzzle: Puzzle) -> list[list]:
    """Return a legal program for puzzle, choosing the shortest local baseline.

    Values never exceed the input limit; the solver uses no randomness or
    external dependencies. A puzzle requiring more than the official program
    limit from this baseline raises ValueError instead of returning an invalid
    submission. The result is not guaranteed to have minimum instruction count.
    """
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    if set(puzzle.targets) - set(puzzle.starting_values) == {1}:
        return [["/", 0, 0, 0]]
    candidates = [_solve_base(puzzle, base) for base in _candidate_bases(puzzle)]
    if not candidates:
        raise ValueError("No constructive base is available for this puzzle")
    best = min(candidates, key=lambda program: (len(program), program))
    if len(best) > 4096:
        raise ValueError(f"Baseline needs {len(best)} instructions; maximum is 4096")
    return best


def _window_terms(value: int, width: int) -> list[tuple[int, int]]:
    """Return (binary shift, signed odd digit) terms, most significant first."""
    digits = []
    modulus = 1 << width
    while value:
        digit = 0
        if value % 2:
            digit = value % modulus
            if digit >= modulus // 2:
                digit -= modulus
            value -= digit
        digits.append(digit)
        value //= 2
    nonzero = [index for index in range(len(digits) - 1, -1, -1) if digits[index]]
    terms = [(0, digits[nonzero[0]])]
    previous = nonzero[0]
    for index in nonzero[1:]:
        terms.append((previous - index, digits[index]))
        previous = index
    if previous:
        terms.append((previous, 0))
    return terms


def _solve_window(puzzle: Puzzle, width: int) -> list[list]:
    """Construct a signed width-w non-adjacent-form candidate.

    A candidate may temporarily exceed the bound before a signed subtraction;
    _Machine rejects it. The public portfolio retains a positive-radix fallback.
    """
    missing = set(puzzle.targets) - set(puzzle.starting_values)
    if not missing:
        return []
    terms = {target: _window_terms(target, width) for target in missing}
    odd_constants = set(range(1, 1 << (width - 1), 2))
    powers = {1 << exponent for exponent in range(1, 11) if 1 << exponent <= puzzle.value_limit}
    machine = _Machine(puzzle, {1, 2} | odd_constants | powers)

    def ensure(value: int, pinned=frozenset()) -> int:
        register = machine.register(value)
        if register is not None:
            return register
        if value > machine.limit:
            raise ValueError("Signed-window constant exceeds value_limit")
        direct = machine.direct(value)
        if direct is not None:
            return machine.emit(*direct, pinned=pinned)
        if value == 1:
            source = next(i for i, item in enumerate(machine.values) if item is not None)
            return machine.emit("/", source, source, pinned=pinned)
        half = ensure(value // 2, pinned)
        doubled = machine.emit("+", half, half, pinned=pinned)
        if value % 2:
            one = ensure(1, pinned | frozenset({doubled}))
            return machine.emit("+", doubled, one, pinned=pinned)
        return doubled

    # Some small puzzles finish before needing any signed representation.
    progress = True
    while progress and machine.remaining:
        progress = False
        for target in sorted(machine.remaining):
            if target not in machine.remaining:
                continue
            direct = machine.direct(target)
            if direct is not None:
                machine.emit(*direct)
                progress = True
    if not machine.remaining:
        return machine.program[:machine.completed_at]

    needed = {1}
    for target in machine.remaining:
        for shift, digit in terms[target]:
            if digit:
                needed.add(abs(digit))
            while shift:
                step = min(10, shift)
                needed.add(1 << step)
                shift -= step
    for constant in sorted(needed):
        ensure(constant)
        if not machine.remaining:
            return machine.program[:machine.completed_at]

    for target in sorted(missing):
        if target not in machine.remaining:
            continue
        direct = machine.direct(target)
        if direct is not None:
            machine.emit(*direct)
            continue
        accumulator = ensure(terms[target][0][1])
        for shift, digit in terms[target][1:]:
            while shift:
                step = min(10, shift)
                factor = ensure(1 << step, frozenset({accumulator}))
                accumulator = machine.emit("*", accumulator, factor)
                shift -= step
            if digit:
                constant = ensure(abs(digit), frozenset({accumulator}))
                accumulator = machine.emit("+" if digit > 0 else "-", accumulator, constant)
        if not machine.remaining:
            break
    if machine.remaining:
        raise ValueError("Signed-window candidate failed to cover all targets")
    if len(machine.program[:machine.completed_at]) > 4096:
        raise ValueError("Signed-window candidate exceeds 4096 instructions")
    return machine.program[:machine.completed_at]


def solve(puzzle: Puzzle, strategy: str = "portfolio") -> list[list]:
    """Choose a valid deterministic radix or signed-window construction.

    ``window`` tries widths 4, 5 and 6, falling back to radix if every signed
    candidate violates a limit. ``portfolio`` retains the shortest candidate
    including radix. ``radix`` reproduces the original constructive baseline.
    """
    if strategy not in {"radix", "window", "portfolio"}:
        raise ValueError(f"Unknown solver strategy: {strategy}")
    if strategy == "radix":
        return solve_radix(puzzle)
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    candidates = []
    for width in (4, 5, 6):
        try:
            candidates.append(_solve_window(puzzle, width))
        except ValueError:
            continue
    if strategy == "portfolio" or not candidates:
        candidates.append(solve_radix(puzzle))
    return min(candidates, key=lambda program: (len(program), program))
