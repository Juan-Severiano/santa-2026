"""Bounded, exact compression of already valid arithmetic programs.

The optimizer lifts destructive register writes into a value graph, preserves
one occurrence of every target, removes dead operations, and tries shorter
recipes made from values live at the rewritten instruction. Every accepted
candidate is allocated back into the puzzle's register file and replayed by
the independent core validator. Input instructions are never mutated.
"""

from __future__ import annotations

import heapq
import random
from collections import Counter, defaultdict
from dataclasses import dataclass

from .core import Puzzle, validate_program


@dataclass
class _Node:
    value: int
    operation: str | None
    left: int
    right: int
    order: int


def _value(operation, first, second):
    if operation == "+":
        return first + second
    if operation == "-":
        return first - second
    if operation == "*":
        return first * second
    return first // second


def _lift(puzzle, program):
    nodes = [_Node(value, None, -1, -1, -1) for value in puzzle.starting_values]
    registers = list(range(len(nodes))) + [None] * (puzzle.registers - len(nodes))
    occurrences = defaultdict(list)
    for index, node in enumerate(nodes):
        occurrences[node.value].append(index)
    for position, (operation, left, right, destination) in enumerate(program):
        first, second = registers[left], registers[right]
        value = _value(operation, nodes[first].value, nodes[second].value)
        index = len(nodes)
        nodes.append(_Node(value, operation, first, second, 4 * position + 4))
        registers[destination] = index
        occurrences[value].append(index)
    roots = {target: occurrences[target][0] for target in set(puzzle.targets)
             if target not in puzzle.starting_values}
    return nodes, roots, occurrences


def _reachable(nodes, roots):
    active = set()
    pending = list(roots)
    while pending:
        index = pending.pop()
        if index in active:
            continue
        active.add(index)
        node = nodes[index]
        if node.operation is not None:
            pending.extend((node.left, node.right))
    return active


def _choose_roots(nodes, roots, occurrences, rounds):
    roots = dict(roots)
    for _ in range(rounds):
        changed = False
        for target in sorted(roots):
            choices = occurrences[target]
            if len(choices) < 2:
                continue
            others = _reachable(nodes, [root for value, root in roots.items() if value != target])
            best, best_cost = roots[target], None
            for candidate in choices:
                additional = _reachable(nodes, [candidate]) - others
                cost = sum(nodes[index].operation is not None for index in additional)
                if best_cost is None or (cost, nodes[candidate].order, candidate) < (
                    best_cost, nodes[best].order, best
                ):
                    best, best_cost = candidate, cost
            if roots[target] != best:
                roots[target] = best
                changed = True
        if not changed:
            break
    return roots


def _schedule(nodes, active):
    indegrees = {}
    successors = defaultdict(list)
    for index in active:
        node = nodes[index]
        if node.operation is None:
            continue
        dependencies = {child for child in (node.left, node.right)
                        if nodes[child].operation is not None}
        indegrees[index] = len(dependencies)
        for child in dependencies:
            successors[child].append(index)
    ready = [(nodes[index].order, index) for index, degree in indegrees.items() if not degree]
    heapq.heapify(ready)
    result = []
    while ready:
        _, index = heapq.heappop(ready)
        result.append(index)
        for successor in successors[index]:
            indegrees[successor] -= 1
            if not indegrees[successor]:
                heapq.heappush(ready, (nodes[successor].order, successor))
    if len(result) != len(indegrees):
        raise ValueError("Cycle in optimizer value graph")
    return result


def _references(nodes, active, roots=()):
    uses = Counter(roots)
    for index in active:
        node = nodes[index]
        if node.operation is not None:
            uses[node.left] += 1
            uses[node.right] += 1
    return uses


def _compile(puzzle, nodes, roots):
    active = _reachable(nodes, roots.values())
    order = _schedule(nodes, active)
    uses = _references(nodes, active)
    locations = {index: index for index in range(len(puzzle.starting_values)) if uses[index]}
    free = set(range(puzzle.registers)) - set(locations.values())
    program = []
    for index in order:
        node = nodes[index]
        first, second = locations[node.left], locations[node.right]
        uses[node.left] -= 1
        uses[node.right] -= 1
        dying = {child for child in (node.left, node.right) if not uses[child]}
        disposable = free | {locations[child] for child in dying}
        if not disposable:
            raise ValueError("Compressed graph exceeds available registers")
        destination = min(disposable)
        for child in dying:
            free.add(locations.pop(child))
        free.discard(destination)
        program.append([node.operation, first, second, destination])
        if uses[index]:
            locations[index] = destination
        else:
            free.add(destination)
    validate_program(puzzle, program)
    return program


def _direct(target, available):
    """Enumerate exact one-operation recipes using the supplied live nodes."""
    seen = set()
    for value, index in available.items():
        possibilities = []
        other = target - value
        if other in available:
            possibilities.append(("+", index, available[other]))
        other = value - target
        if other in available:
            possibilities.append(("-", index, available[other]))
        other = target + value
        if other in available:
            possibilities.append(("-", available[other], index))
        if target % value == 0 and target // value in available:
            possibilities.append(("*", index, available[target // value]))
        if value % target == 0 and value // target in available:
            possibilities.append(("/", index, available[value // target]))
        other = target * value
        if other in available:
            possibilities.append(("/", available[other], index))
        for recipe in possibilities:
            if recipe not in seen:
                seen.add(recipe)
                yield recipe


def _removed_cost(nodes, references, original, added):
    """Count operations orphaned by replacing a node's two dependency edges."""
    counts = dict(Counter(added))
    node = nodes[original]
    pending = [node.left, node.right]
    removed = 0
    while pending:
        child = pending.pop()
        counts[child] = counts.get(child, 0) - 1
        if references[child] + counts[child] == 0 and nodes[child].operation is not None:
            removed += 1
            pending.extend((nodes[child].left, nodes[child].right))
    return removed


def _live_snapshots(nodes, active, order):
    uses = _references(nodes, active)
    live = {index for index in active if nodes[index].operation is None and uses[index]}
    snapshots = {}
    for index in order:
        snapshots[index] = {nodes[child].value: child for child in sorted(live)}
        node = nodes[index]
        for child in (node.left, node.right):
            uses[child] -= 1
            if not uses[child]:
                live.discard(child)
        if uses[index]:
            live.add(index)
    return snapshots


def _rewrite(nodes, roots, puzzle, rng, rounds, two_steps):
    root_set = set(roots.values())
    for _ in range(rounds):
        active = _reachable(nodes, roots.values())
        order = _schedule(nodes, active)
        references = _references(nodes, active, roots.values())
        snapshots = _live_snapshots(nodes, active, order)
        candidates = list(reversed(order))
        if rng is not None:
            # Prefer target sinks, varying their order across restarts.
            target_nodes = [index for index in candidates if index in root_set]
            rng.shuffle(target_nodes)
            candidates = target_nodes + [index for index in candidates if index not in root_set]
        changed = False
        for index in candidates:
            node = nodes[index]
            available = snapshots[index]
            best = None
            best_gain = 0
            for operation, first, second in _direct(node.value, available):
                gain = _removed_cost(nodes, references, index, (first, second))
                if gain > best_gain:
                    best = operation, first, second
                    best_gain = gain
            if best is not None:
                node.operation, node.left, node.right = best
                changed = True
                break
            if not two_steps or index not in root_set:
                continue
            anchors = sorted(available, key=lambda value: (value > 64, abs(value - node.value), value))[:24]
            two_best = None
            for anchor in anchors:
                anchor_node = available[anchor]
                recipes = [(node.value - anchor, "+", False),
                           (anchor - node.value, "-", True),
                           (node.value + anchor, "-", False)]
                if node.value % anchor == 0:
                    recipes.append((node.value // anchor, "*", False))
                if anchor % node.value == 0:
                    recipes.append((anchor // node.value, "/", True))
                recipes.append((node.value * anchor, "/", False))
                for middle, final_operation, reversed_operands in recipes:
                    if not 1 <= middle <= puzzle.value_limit or middle in available:
                        continue
                    for operation, first, second in _direct(middle, available):
                        gain = _removed_cost(nodes, references, index, (first, second, anchor_node)) - 1
                        if gain > best_gain:
                            best_gain = gain
                            two_best = (middle, operation, first, second, anchor_node,
                                        final_operation, reversed_operands)
            if two_best is not None:
                middle, operation, first, second, anchor, final_op, reverse = two_best
                new_index = len(nodes)
                nodes.append(_Node(middle, operation, first, second, node.order - 1))
                node.operation = final_op
                node.left, node.right = (anchor, new_index) if reverse else (new_index, anchor)
                changed = True
                break
        if not changed:
            break


def _common_values(nodes, roots):
    """Merge equal-valued graph nodes; allocation may reject excess liveness."""
    replacements = {}
    canonical = {}
    result = []
    for index, node in enumerate(nodes):
        previous = canonical.get(node.value)
        if previous is None:
            canonical[node.value] = index
            replacements[index] = index
        else:
            replacements[index] = previous
        result.append(_Node(node.value, node.operation,
                            replacements.get(node.left, node.left),
                            replacements.get(node.right, node.right), node.order))
    # Rewrites append nodes whose numeric IDs can exceed a parent's ID. A
    # second remapping pass handles those forward references as well.
    for node in result:
        node.left = replacements.get(node.left, node.left)
        node.right = replacements.get(node.right, node.right)
    return result, {target: replacements[index] for target, index in roots.items()}


def optimize_program(puzzle: Puzzle, program: list[list], seed=0, effort=1) -> list[list]:
    """Return a validated program no longer than the supplied valid program.

    Work is bounded by ``effort`` (clamped to 1..8). The seed varies greedy
    rewrite order and is reproducible. No external processes or data are used.
    Invalid inputs raise ValueError; allocation failures retain the last valid
    candidate rather than weakening the calculator constraints.
    """
    validate_program(puzzle, program)
    best = [instruction[:] for instruction in program]
    if not best:
        return best
    effort = max(1, min(8, int(effort)))
    nodes, roots, occurrences = _lift(puzzle, best)
    roots = _choose_roots(nodes, roots, occurrences, min(4, effort + 1))

    def accept(graph, sinks):
        nonlocal best
        try:
            candidate = _compile(puzzle, graph, sinks)
        except (ValueError, KeyError):
            return
        if len(candidate) < len(best):
            best = candidate

    accept(nodes, roots)
    merged, merged_roots = _common_values(nodes, roots)
    accept(merged, merged_roots)
    # Re-lift the shortest valid candidate so attempted CSE that required too
    # many registers cannot influence the bounded local rewrite search.
    nodes, roots, _ = _lift(puzzle, best)
    _rewrite(nodes, roots, puzzle, random.Random(seed), min(64, 12 * effort), False)
    accept(nodes, roots)
    if effort >= 1:
        nodes, roots, _ = _lift(puzzle, best)
        _rewrite(nodes, roots, puzzle, random.Random(seed ^ 0x5A17), min(24, 3 * effort), True)
        accept(nodes, roots)
    validate_program(puzzle, best)
    return best
