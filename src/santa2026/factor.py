"""Bounded nearby factor circuits for exact positive arithmetic programs.

Each target can use a factored nearby integer plus or minus a cheap live
residual. Small trial factors, bounded Pollard-rho attempts, and large cheap
power-of-two neighbours supply alternative circuits. Factorization is only a
search heuristic: uncertain or prime remainders need no special assumption,
and every returned program is replayed with exact integers.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from .core import Puzzle, validate_program
from .solver import _Machine, solve
from .synthesis import _Expression, _Planner


_SMALL_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47,
                 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107,
                 109, 113, 127, 131, 137, 139, 149, 151, 157, 163, 167,
                 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229,
                 233, 239, 241, 251, 257)


def _is_prime(value):
    """Deterministic Miller-Rabin for unsigned 64-bit integers."""
    if value < 2:
        return False
    for prime in _SMALL_PRIMES[:12]:
        if value % prime == 0:
            return value == prime
    odd, exponent = value - 1, 0
    while odd % 2 == 0:
        odd //= 2
        exponent += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        if base % value == 0:
            continue
        residue = pow(base, odd, value)
        if residue in (1, value - 1):
            continue
        for _ in range(exponent - 1):
            residue = residue * residue % value
            if residue == value - 1:
                break
        else:
            return False
    return True


def _rho(value, steps):
    """Find one exact factor within a fixed modular-operation budget."""
    for prime in (2, 3, 5):
        if value % prime == 0:
            return prime
    rng = random.Random(value ^ 0xFACADE)
    for _ in range(2):
        constant = rng.randrange(1, value)
        first = second = rng.randrange(2, value)
        product = 1
        for step in range(max(1, steps // 2)):
            first = (first * first + constant) % value
            second = (second * second + constant) % value
            second = (second * second + constant) % value
            product = product * abs(first - second) % value
            if step % 16 == 15:
                divisor = math.gcd(product, value)
                if 1 < divisor < value:
                    return divisor
                if divisor == value:
                    break
                product = 1
        divisor = math.gcd(product, value)
        if 1 < divisor < value:
            return divisor
    return None


@lru_cache(maxsize=16384)
def _factor_pairs(value, effort):
    """Return useful exact divisor pairs; full factorization is unnecessary."""
    if value < 4:
        return ()
    remainder = value
    factors = []
    for prime in _SMALL_PRIMES:
        while remainder % prime == 0:
            factors.append(prime)
            remainder //= prime
        if prime * prime > remainder:
            break
    if remainder > 1:
        if effort > 0 and not _is_prime(remainder):
            divisor = _rho(remainder, 192 * min(effort, 4))
            if divisor:
                factors.extend((divisor, remainder // divisor))
            else:
                factors.append(remainder)
        else:
            factors.append(remainder)
    divisors = {1}
    for factor in factors:
        divisors.update(divisor * factor for divisor in tuple(divisors))
        if len(divisors) > 1024:
            root = math.isqrt(value)
            by_balance = sorted(divisors, key=lambda divisor: abs(divisor - root))[:256]
            by_small = sorted(divisors)[:128]
            divisors = set(by_balance + by_small)
    pairs = {(min(divisor, value // divisor), max(divisor, value // divisor))
             for divisor in divisors if 1 < divisor < value and value % divisor == 0}
    # Low popcount is only a cheap tie-break; the planner evaluates actual
    # signed-window expressions for the retained divisor pairs.
    return tuple(sorted(pairs, key=lambda pair: (
        pair[0].bit_length() + pair[1].bit_length(),
        bin(pair[0]).count("1") + bin(pair[1]).count("1"),
        abs(pair[0].bit_length() - pair[1].bit_length()), pair
    ))[:32])


class _FactorPlanner(_Planner):
    def __init__(self, machine, initial, effort, seed):
        super().__init__(machine, initial, effort)
        self.seed = seed
        self.powers = {}

    def power(self, exponent):
        value = 1 << exponent
        if value in self.known:
            return self.leaves[value]
        if exponent in self.powers:
            return self.powers[exponent]
        if exponent <= 10:
            result = self.basic(value)
        elif exponent % 2 == 0:
            half = self.power(exponent // 2)
            result = self.node("*", half, half)
        else:
            result = self.node("*", self.power(exponent - 1), self.leaves[2])
        self.powers[exponent] = result
        return result

    def construct(self, value):
        best = self.basic(value)
        if best.cost <= 2:
            return best
        # Large powers and their neighbours have short construction circuits
        # despite their magnitude; their setup is reused by later targets.
        exponents = (12, 16, 20, 24, 32) if self.effort < 2 else (12, 14, 16, 18, 20, 24, 28, 32, 40)
        multipliers = []
        for exponent in exponents:
            power = 1 << exponent
            if power > value or power > self.limit:
                continue
            power_plan = self.power(exponent)
            multipliers.append(power_plan)
            for operation in ("+", "-"):
                try:
                    multipliers.append(self.node(operation, power_plan, self.leaves[1]))
                except ValueError:
                    continue
        for multiplier in multipliers:
            quotient, remainder = divmod(value, multiplier.value)
            if not quotient:
                continue
            product = self.node("*", self.basic(quotient), multiplier)
            candidate = self.node("+", product, self.basic(remainder)) if remainder else product
            if candidate.cost < best.cost:
                best = candidate
            if remainder and (quotient + 1) * multiplier.value <= self.limit:
                product = self.node("*", self.basic(quotient + 1), multiplier)
                candidate = self.node("-", product, self.basic(multiplier.value - remainder))
                if candidate.cost < best.cost:
                    best = candidate

        residuals = [0]
        cheap = sorted(value for value in self.known if value <= 1024)
        preferred = [1, 2, 3, 5, 7, 8, 15, 16, 31, 32, 64, 128, 256, 512, 1024]
        preferred = [value for value in preferred if value in self.known]
        if self.seed % 3:
            random.Random(self.seed).shuffle(cheap)
            preferred.extend(value for value in cheap if value not in preferred)
        budget = 8 + 4 * min(self.effort, 4)
        for residual in preferred[:budget]:
            residuals.extend((residual, -residual))
        for residual in residuals:
            nearby = value - residual
            if not 1 < nearby <= self.limit:
                continue
            # Expensive attempts concentrate on the target and unit offsets;
            # all other offsets still get trial factors and divisor products.
            rho_effort = self.effort if abs(residual) <= 3 else 0
            for first, second in _factor_pairs(nearby, rho_effort):
                first_plan, second_plan = self.basic(first), self.basic(second)
                product = self.node("*", first_plan, second_plan)
                if residual:
                    candidate = self.node("+" if residual > 0 else "-", product,
                                          self.basic(abs(residual)))
                else:
                    candidate = product
                if candidate.cost < best.cost:
                    best = candidate
        return best


def _solve_factored(puzzle, seed, effort):
    maximum = min(puzzle.value_limit, max(puzzle.targets, default=1))
    protected = set(puzzle.starting_values) | {1}
    protected.update(value for value in range(3, 32, 2) if value <= maximum)
    protected.update(1 << exponent for exponent in range(1, 11) if 1 << exponent <= maximum)
    machine = _Machine(puzzle, protected)

    def constant(value, pinned=frozenset()):
        found = machine.register(value)
        if found is not None:
            return found
        direct = machine.direct(value)
        if direct:
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

    def emit(expression: _Expression, pinned=frozenset()):
        found = machine.register(expression.value)
        if found is not None:
            return found
        direct = machine.direct(expression.value)
        if direct:
            return machine.emit(*direct, pinned=pinned)
        if expression.op is None:
            replacement = _Planner(machine, puzzle.starting_values, 1).basic(expression.value)
            if replacement.op is None:
                raise ValueError("Factor circuit lost an unavailable leaf")
            return emit(replacement, pinned)
        left = emit(expression.left, pinned)
        right = emit(expression.right, pinned | frozenset({left}))
        return machine.emit(expression.op, left, right, pinned=pinned)

    targets = sorted(machine.remaining)
    if seed % 4 == 1:
        targets.reverse()
    elif seed % 4 == 2:
        random.Random(seed).shuffle(targets)
    elif seed % 4 == 3:
        targets = targets[::2] + list(reversed(targets[1::2]))
    for target in targets:
        if target not in machine.remaining:
            continue
        planner = _FactorPlanner(machine, puzzle.starting_values, effort, seed)
        emit(planner.construct(target))
        if not machine.remaining:
            break
    if machine.remaining:
        raise ValueError("Factor circuits did not cover every target")
    return machine.program[:machine.completed_at]


def solve_factor(puzzle: Puzzle, seed: int = 0, effort: int = 1) -> list[list]:
    """Return an exact nearby-factor candidate, retaining signed fallback."""
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    fallback = solve(puzzle, strategy="window")
    try:
        candidate = _solve_factored(puzzle, seed, min(effort, 4))
        validate_program(puzzle, candidate)
    except ValueError:
        return fallback
    return candidate if len(candidate) < len(fallback) else fallback
