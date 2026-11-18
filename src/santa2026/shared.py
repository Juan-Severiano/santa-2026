"""Shared arithmetic intermediates and bounded register scheduling.

The planner scores a candidate intermediate against every remaining target.
Positive aggregate savings cause it to build that intermediate once, then
schedule its useful target expressions while its register remains pinned.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from fractions import Fraction
from functools import lru_cache

from .core import Puzzle
from .solver import _Machine, _window_terms


@lru_cache(maxsize=200000)
def _terms(value: int, width: int):
    return tuple(_window_terms(value, width))


def _affine(target: int, anchor: int, limit: int):
    """Represent target as q*anchor +/- r, with legal multiplication."""
    quotient, remainder = divmod(target, anchor)
    if quotient:
        yield quotient, remainder
    if remainder and (quotient + 1) * anchor <= limit:
        yield quotient + 1, remainder - anchor


def _candidates(puzzle: Puzzle, effort: int):
    targets = sorted(set(puzzle.targets))
    values = set(targets) | set(puzzle.starting_values)
    for left in puzzle.starting_values:
        for right in puzzle.starting_values:
            if left * right <= puzzle.value_limit:
                values.add(left * right)
    for index, target in enumerate(targets):
        for divisor in range(2, 33 if effort < 3 else 65):
            if target % divisor == 0:
                values.add(target // divisor)
        for other in targets[:index]:
            difference = target - other
            if difference:
                values.add(difference)
            common = math.gcd(target, other)
            if common > 1:
                values.add(common)
            # Approximate common factors: t ~= a*g and u ~= b*g. Rounding
            # t/a to an integer exposes candidate g even when neither target
            # is exactly divisible by its small coefficient.
            if effort >= 2 and other > 1000:
                for denominator in range(2, 17):
                    numerator = (target * denominator + other // 2) // other
                    if numerator <= 32 and abs(target * denominator - other * numerator) <= 1000 * (denominator + numerator):
                        values.add((other + denominator // 2) // denominator)
    # Several targets may have distinct small noises around small multiples
    # of one latent value. Intersect their narrow integer intervals instead
    # of testing every possible noise pair or relying on exact pairwise gcd.
    radius = 32 if effort < 2 else 64
    latent = {}
    for index, target in enumerate(targets):
        seen = set()
        for divisor in range(1, 33):
            lower = max(33, (target - radius + divisor - 1) // divisor)
            upper = (target + radius) // divisor
            seen.update(range(lower, upper + 1))
        flag = 1 << index
        for value in seen:
            latent[value] = latent.get(value, 0) | flag
    values.update(value for value, mask in latent.items() if mask & (mask - 1))
    return sorted(value for value in values if 1 < value <= puzzle.value_limit)


def _wide_latent_candidates(puzzle: Puzzle):
    """Recover plausible common factors when coefficients exceed 32."""
    targets = sorted(set(puzzle.targets))
    candidates = set()
    for index, target in enumerate(targets):
        for other in targets[:index]:
            for bound in (128, 512, 2048, 8192):
                fraction = Fraction(target, other).limit_denominator(bound)
                numerator, denominator = fraction.numerator, fraction.denominator
                if numerator > 8192 or abs(target * denominator - other * numerator) > 1000 * (numerator + denominator):
                    continue
                for value in (target // numerator, other // denominator):
                    if value > 1024:
                        candidates.update((value - 1, value, value + 1))
    return candidates


class _Planner:
    def __init__(self, puzzle: Puzzle, seed: int, effort: int):
        self.puzzle = puzzle
        self.width = (5, 6, 4, 7, 8)[seed % 5] if seed < 5 else 5
        self.high_powers = seed >= 10
        self.dictionary_first = seed >= 5 and seed != 10
        self.bank_size = ((0, 4, 8, 12)[(seed - 10) % 4] if self.high_powers
                          else (4, 8, 12, 16)[(seed - 5) % 4]) if self.dictionary_first else 0
        self.effort = effort
        self.rng = random.Random(seed)
        # Width seven's full odd table still fits in 48 registers. Wider
        # windows retain only their hot low digits; other digits use the same
        # recyclable cache as intermediate values.
        odd_limit = min(1 << (self.width - 1), 64 if self.width == 7 else 32)
        if self.width >= 8:
            frequencies = Counter(abs(digit) for target in puzzle.targets for _, digit in _terms(target, self.width) if digit)
            hot_count = 24 if seed < 5 else 32
            odds = {digit for digit, _ in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:hot_count]}
            odds.add(1)
        else:
            odds = set(range(1, odd_limit, 2))
        powers = {1 << exponent for exponent in range(1, 11) if 1 << exponent <= puzzle.value_limit}
        self.extra_powers = {1 << exponent for exponent in (12, 16, 20, 24, 32, 40, 48)
                             if 1 << exponent <= puzzle.value_limit} if self.high_powers else set()
        powers.update(self.extra_powers)
        self.machine = _Machine(puzzle, {1, 2} | odds | powers)
        self.helpers = _candidates(puzzle, effort)
        if self.dictionary_first:
            self.helpers = sorted(set(self.helpers) | _wide_latent_candidates(puzzle))
        self.estimate_epoch = -1
        self.estimate_cache = {}
        self.pairs_epoch = -1
        self.pairs_cache = {}

    def small(self, value: int, pinned=frozenset()):
        found = self.machine.register(value)
        if found is not None:
            return found
        direct = self.machine.direct(value)
        if direct is not None:
            return self.machine.emit(*direct, pinned=pinned)
        if value == 1:
            source = next(i for i, v in enumerate(self.machine.values) if v is not None)
            return self.machine.emit("/", source, source, pinned=pinned)
        if self.high_powers and value & (value - 1) == 0 and value > 2:
            exponent = value.bit_length() - 1
            left = self.small(1 << (exponent // 2), pinned)
            right = self.small(1 << (exponent - exponent // 2), pinned | frozenset({left}))
            return self.machine.emit("*", left, right, pinned=pinned)
        half = self.small(value // 2, pinned)
        doubled = self.machine.emit("+", half, half, pinned=pinned)
        if value % 2:
            one = self.small(1, pinned | frozenset({doubled}))
            return self.machine.emit("+", doubled, one, pinned=pinned)
        return doubled

    def build(self, value: int, pinned=frozenset()):
        found = self.machine.register(value)
        if found is not None:
            return found
        direct = self.machine.direct(value)
        if direct is not None:
            return self.machine.emit(*direct, pinned=pinned)
        if self.high_powers:
            short = self.two_steps(value)
            if short is not None:
                return self.emit_two_steps(short, pinned)
        if value < max(33, 1 << (self.width - 1)):
            return self.small(value, pinned)
        terms = _terms(value, self.width)
        accumulator_value = terms[0][1]
        safe = True
        for shift, digit in terms[1:]:
            accumulator_value <<= shift
            if accumulator_value > self.machine.limit:
                safe = False
                break
            accumulator_value += digit
        if not safe:
            # Unsigned binary never exceeds its final value.
            accumulator = self.small(1, pinned)
            one = accumulator
            for bit in bin(value)[3:]:
                accumulator = self.machine.emit("+", accumulator, accumulator, pinned=pinned)
                if bit == "1":
                    one = self.small(1, pinned | frozenset({accumulator}))
                    accumulator = self.machine.emit("+", accumulator, one, pinned=pinned)
            return accumulator
        accumulator = self.small(terms[0][1], pinned)
        for shift, digit in terms[1:]:
            while shift:
                step = min(10, shift)
                multiplier = self.small(1 << step, pinned | frozenset({accumulator}))
                accumulator = self.machine.emit("*", accumulator, multiplier, pinned=pinned)
                shift -= step
            if digit:
                constant = self.small(abs(digit), pinned | frozenset({accumulator}))
                accumulator = self.machine.emit("+" if digit > 0 else "-", accumulator, constant, pinned=pinned)
        return accumulator

    def estimate(self, value: int):
        epoch = len(self.machine.program)
        if epoch != self.estimate_epoch:
            self.estimate_cache.clear()
            self.estimate_epoch = epoch
        found = self.estimate_cache.get(value)
        if found is not None:
            return found
        result = self._estimate(value)
        self.estimate_cache[value] = result
        return result

    def _estimate(self, value: int):
        if not value:
            return 0
        if value in self.machine.locations:
            return 0
        if value <= 2048 and self.machine.direct(value) is not None:
            return 1
        if value < max(33, 1 << (self.width - 1)):
            # Constants are inexpensive, but charging for missing constants
            # avoids choosing a helper solely through a large unpaid table.
            return max(1, value.bit_length() - 1)
        terms = _terms(value, self.width)
        total = self.estimate(terms[0][1])
        accumulator = terms[0][1]
        for shift, digit in terms[1:]:
            accumulator <<= shift
            if accumulator > self.machine.limit:
                return value.bit_length() + bin(value).count("1") - 2
            total += (shift + 9) // 10 + bool(digit)
            accumulator += digit
            if accumulator in self.machine.locations:
                total = 0
        return total

    def construction_cost(self, value: int):
        if value in self.machine.locations:
            return 0
        if self.machine.direct(value) is not None:
            return 1
        best = self.estimate(value)
        for anchor in self.machine.locations:
            if anchor > 1 and anchor != value:
                best = min(best, self.relation_cost(value, anchor)[0])
        return best

    def two_steps(self, target: int):
        """Find an exact two-operation expression over currently live values."""
        epoch = len(self.machine.program)
        if epoch != self.pairs_epoch:
            pairs = {}
            values = sorted(self.machine.locations)
            registers = {value: self.machine.register(value) for value in values}
            for left in values:
                for right in values:
                    a, b = registers[left], registers[right]
                    if left + right <= self.machine.limit:
                        pairs.setdefault(left + right, ("+", a, b))
                    if left > right:
                        pairs.setdefault(left - right, ("-", a, b))
                    if left * right <= self.machine.limit:
                        pairs.setdefault(left * right, ("*", a, b))
                    if left % right == 0:
                        pairs.setdefault(left // right, ("/", a, b))
            self.pairs_cache = pairs
            self.pairs_epoch = epoch
        for anchor in sorted(self.machine.locations):
            source = self.machine.register(anchor)
            candidates = []
            if target > anchor:
                candidates.append((target - anchor, "+", False))
            if anchor > target:
                candidates.append((anchor - target, "-", False))
            if target + anchor <= self.machine.limit:
                candidates.append((target + anchor, "-", True))
            if target % anchor == 0:
                candidates.append((target // anchor, "*", False))
            if anchor % target == 0:
                candidates.append((anchor // target, "/", False))
            if target * anchor <= self.machine.limit:
                candidates.append((target * anchor, "/", True))
            for argument, operation, reverse in candidates:
                if argument == target or argument in self.machine.locations:
                    continue
                first = self.pairs_cache.get(argument)
                if first is not None:
                    return first, operation, source, reverse
        return None

    def emit_two_steps(self, plan, pinned=frozenset()):
        first, operation, source, reverse = plan
        argument = self.machine.emit(*first, pinned=pinned | frozenset({source}))
        left, right = (argument, source) if reverse else (source, argument)
        return self.machine.emit(operation, left, right, pinned=pinned)

    def relation_cost(self, target: int, anchor: int):
        if target == anchor:
            return 0, 1, 0
        best = (10**9, 0, 0)
        for quotient, residual in _affine(target, anchor, self.machine.limit):
            cost = (0 if quotient == 1 else self.estimate(quotient) + 1)
            cost += self.estimate(abs(residual)) + bool(residual)
            candidate = (cost, quotient, residual)
            if candidate < best:
                best = candidate
        return best

    def produce(self, target: int, anchor=None, pinned=frozenset()):
        if target in self.machine.locations:
            return self.machine.register(target)
        direct = self.machine.direct(target)
        if direct is not None:
            return self.machine.emit(*direct, pinned=pinned)
        two_steps = self.two_steps(target)
        if two_steps is not None:
            return self.emit_two_steps(two_steps, pinned)
        best = (self.estimate(target), 0, 0, None)
        anchors = [anchor] if anchor is not None else sorted(self.machine.locations)
        for value in anchors:
            if value <= 1 or value == target:
                continue
            cost, quotient, residual = self.relation_cost(target, value)
            if quotient and cost < best[0]:
                best = cost, quotient, residual, value
        _, quotient, residual, value = best
        if value is None:
            return self.build(target, pinned)
        source = self.machine.register(value)
        keep = pinned | frozenset({source})
        if quotient == 1:
            product = source
        else:
            multiplier = self.build(quotient, keep)
            product = self.machine.emit("*", source, multiplier, pinned=pinned)
        if residual:
            remainder = self.build(abs(residual), pinned | frozenset({product}))
            product = self.machine.emit("+" if residual > 0 else "-", product, remainder, pinned=pinned)
        return product

    def select_helper(self):
        remaining = sorted(self.machine.remaining)
        base_costs = {target: self.construction_cost(target) for target in remaining}
        best = None
        for helper in self.helpers:
            if helper in self.machine.locations:
                continue
            savings = 0
            beneficiaries = []
            for target in remaining:
                cost = self.relation_cost(target, helper)[0]
                gain = base_costs[target] - cost
                if gain > 0:
                    savings += gain
                    beneficiaries.append(target)
            if len(beneficiaries) < 2 or savings <= 2:
                continue
            net = savings - self.construction_cost(helper)
            if net > 2:
                rank = (net, len(beneficiaries), -helper)
                if best is None or rank > best[0]:
                    best = rank, helper, beneficiaries
        return best

    def run(self):
        for value in sorted(self.extra_powers):
            self.small(value)
            if not self.machine.remaining:
                return _prune(self.puzzle, self.machine.program[:self.machine.completed_at])
        if self.dictionary_first:
            # Keep a joint dictionary live before solving its beneficiaries.
            # Later selected values can become factors/quotients of earlier
            # values, so two-operation closure can use cross-products that a
            # single-anchor star cannot realize without recomputation.
            for _ in range(self.bank_size):
                helper = self.select_helper()
                if helper is None:
                    break
                _, value, _ = helper
                self.produce(value)
                self.machine.protected.add(value)
                if not self.machine.remaining:
                    result = self.machine.program[:self.machine.completed_at]
                    return _prune(self.puzzle, result) if self.high_powers else result
        rounds = 0
        while self.machine.remaining:
            progressed = True
            while progressed and self.machine.remaining:
                progressed = False
                for target in sorted(self.machine.remaining):
                    direct = self.machine.direct(target)
                    if direct is not None:
                        self.machine.emit(*direct)
                        progressed = True
            if not self.machine.remaining:
                break
            short = None
            for target in sorted(self.machine.remaining):
                plan = self.two_steps(target)
                if plan is not None:
                    short = plan
                    break
            if short is not None:
                self.emit_two_steps(short)
                continue
            helper = self.select_helper() if rounds < 5 + self.effort else None
            rounds += 1
            if helper is not None:
                _, value, beneficiaries = helper
                register = self.produce(value)
                keep = frozenset({register})
                for target in sorted(beneficiaries):
                    if target in self.machine.remaining:
                        self.produce(target, value, keep)
                continue
            # Cheap goals first make them available as anchors for later goals.
            targets = sorted(self.machine.remaining)
            if self.effort > 1:
                self.rng.shuffle(targets)
            target = min(targets, key=lambda t: self.estimate(t))
            self.produce(target)
        result = self.machine.program[:self.machine.completed_at]
        return _prune(self.puzzle, result) if self.high_powers else result


def _prune(puzzle: Puzzle, program):
    """Keep dependencies of the first occurrence of each required target."""
    versions = [None] * puzzle.registers
    values = list(puzzle.starting_values) + [None] * (puzzle.registers - len(puzzle.starting_values))
    needed_targets = set(puzzle.targets) - set(puzzle.starting_values)
    parents = []
    roots = []
    for index, (operation, left, right, destination) in enumerate(program):
        parents.append((versions[left], versions[right]))
        a, b = values[left], values[right]
        result = a + b if operation == "+" else a - b if operation == "-" else a * b if operation == "*" else a // b
        values[destination] = result
        versions[destination] = index
        if result in needed_targets:
            needed_targets.remove(result)
            roots.append(index)
    live = set()
    while roots:
        index = roots.pop()
        if index is None or index in live:
            continue
        live.add(index)
        roots.extend(parents[index])
    return [instruction for index, instruction in enumerate(program) if index in live]


def solve_shared(puzzle: Puzzle, seed=0, effort=1):
    """Return a candidate using globally selected shared arithmetic anchors.

    Seeds 0..4 select signed widths 5, 6, 4, 7 and 8. Seeds 5..9 reserve a
    width-five joint dictionary; seeds 10 and above also offer powers through
    2**48 and remove unused setup. Keep a portfolio fallback: these candidates
    are not guaranteed to improve a supplied program or minimize its length.
    """
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    result = _Planner(puzzle, seed, effort).run()
    if len(result) > 4096:
        raise ValueError("Shared candidate exceeds 4096 instructions")
    return result
