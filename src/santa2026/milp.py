"""Global exact recipe selection using SciPy's compiled HiGHS MILP solver.

The public function remains importable without SciPy. Installing the optional
laboratory dependency enables binary value/recipe selection; absence of that
dependency, solver timeouts without an incumbent, or excess register pressure
retain the validated input program.
"""

from __future__ import annotations

import heapq
import random
import warnings
from collections import defaultdict

from .core import Puzzle, validate_program
from .optimizer import _Node, _compile, _lift


def _extra_values(puzzle, values, nonseeds, original, ranks, first, budget, rank_gap=0.5):
    """Find new one-op helpers useful in two-op recipes for old values."""
    known = set(values)
    producers = {}

    def offer(value, operation, left, right):
        if value in known or not 1 <= value <= puzzle.value_limit:
            return
        if operation in ("+", "*") and left > right:
            left, right = right, left
        key = max(ranks[left], ranks[right]), operation, left, right
        previous = producers.get(value)
        if previous is None or key < previous:
            producers[value] = key

    for position, left in enumerate(values):
        for right in values[position:]:
            offer(left + right, "+", left, right)
            offer(left * right, "*", left, right)
            if left > right:
                offer(left - right, "-", left, right)
            elif right > left:
                offer(right - left, "-", right, left)
            larger, smaller = max(left, right), min(left, right)
            if larger % smaller == 0:
                offer(larger // smaller, "/", larger, smaller)
    ancestors = {value: set() for value in puzzle.starting_values}
    for value in nonseeds:
        _, left, right = original[value]
        ancestors[value] = ancestors[left] | ancestors[right] | {value}
    benefits = defaultdict(dict)
    for result in nonseeds:
        for anchor in values:
            if ranks[anchor] >= ranks[result]:
                continue
            middle_values = [result - anchor, result + anchor, anchor - result]
            if result % anchor == 0:
                middle_values.append(result // anchor)
            if anchor % result == 0:
                middle_values.append(anchor // result)
            if result * anchor <= puzzle.value_limit:
                middle_values.append(result * anchor)
            for middle in middle_values:
                producer = producers.get(middle)
                if producer is None or producer[0] + rank_gap >= ranks[result]:
                    continue
                _, _, left, right = producer
                saving = len(ancestors[result] - ancestors[left] - ancestors[right] - ancestors[anchor]) - 2
                if saving >= 0:
                    benefits[middle][result] = max(benefits[middle].get(result, -1), saving)
    target_set = set(puzzle.targets)
    selected = sorted(benefits, key=lambda value: (
        -sum(benefits[value].values()),
        -sum(result in target_set for result in benefits[value]),
        -len(benefits[value]), value
    ))[:budget]
    for value in selected:
        rank, operation, left, right = producers[value]
        ranks[value] = rank + rank_gap
        first[value] = max(first[left], first[right]) + 1
        original[value] = operation, left, right
    return selected


def _problem(puzzle, program, seed, effort=1):
    nodes, _, _ = _lift(puzzle, program)
    seeds = set(puzzle.starting_values)
    values = list(dict.fromkeys(node.value for node in nodes))
    first = {}
    original = {}
    for node in nodes:
        if node.value not in first:
            first[node.value] = node.order
            if node.operation is not None:
                original[node.value] = (node.operation, nodes[node.left].value, nodes[node.right].value)
    nonseeds = [value for value in values if value not in seeds]
    heights = {value: 0 for value in seeds}
    for value in nonseeds:
        _, left, right = original[value]
        heights[value] = 1 + max(heights[left], heights[right])
    if seed % 4 == 0:
        order = sorted(nonseeds, key=lambda value: (heights[value], first[value], value))
    elif seed % 4 == 1:
        order = nonseeds
    else:
        rng = random.Random(seed)
        successors = defaultdict(list)
        degrees = {}
        for value in nonseeds:
            _, left, right = original[value]
            dependencies = {child for child in (left, right) if child not in seeds}
            degrees[value] = len(dependencies)
            for child in dependencies:
                successors[child].append(value)
        ready = [(rng.random(), value) for value in nonseeds if not degrees[value]]
        heapq.heapify(ready)
        order = []
        while ready:
            _, value = heapq.heappop(ready)
            order.append(value)
            for successor in successors[value]:
                degrees[successor] -= 1
                if not degrees[successor]:
                    heapq.heappush(ready, (rng.random(), successor))
    ranks = {value: -1 for value in seeds}
    ranks.update({value: index for index, value in enumerate(order)})
    unrestricted = seed % 4 == 3
    enhanced = seed >= 4
    first_budget = min(64, 32 + 8 * effort) if enhanced else min(64, 24 + 8 * effort)
    extra = _extra_values(puzzle, values, nonseeds, original, ranks, first,
                          first_budget, 0.25 if enhanced else 0.5)
    values.extend(extra)
    nonseeds.extend(extra)
    if enhanced:
        # A second generation can depend on first-generation helper values.
        # These are genuine two-operation helper circuits rather than more
        # alternative recipes over the input program's fixed value set.
        second = _extra_values(puzzle, values, nonseeds, original, ranks, first,
                               min(64, 128 - len(extra)), 0.125)
        values.extend(second)
        nonseeds.extend(second)
    recipes = {}
    all_values = set(values)
    for result in nonseeds:
        candidates = set()
        for left in values:
            possibilities = []
            right = result - left
            if right in all_values and left <= right:
                possibilities.append(("+", left, right))
            right = left - result
            if right in all_values:
                possibilities.append(("-", left, right))
            if result % left == 0:
                right = result // left
                if right in all_values and left <= right:
                    possibilities.append(("*", left, right))
            if left % result == 0:
                right = left // result
                if right in all_values:
                    possibilities.append(("/", left, right))
            for operation, first_source, second_source in possibilities:
                if result in (first_source, second_source):
                    continue
                if unrestricted or max(ranks[first_source], ranks[second_source]) < ranks[result]:
                    candidates.add((operation, first_source, second_source))
        # The first original recipe is legal under every original-compatible
        # ordering, and makes the binary model feasible independently of search.
        candidates.add(original[result])
        recipes[result] = sorted(candidates)
    return values, nonseeds, recipes, first, ranks, unrestricted


def _selected_program(puzzle, nonseeds, chosen, first, ranks, prefer_rank=False):
    graph = [_Node(value, None, -1, -1, -1) for value in puzzle.starting_values]
    locations = {}
    for index, value in enumerate(puzzle.starting_values):
        locations.setdefault(value, index)
    for value in nonseeds:
        if value in chosen:
            locations[value] = len(graph)
            order = ranks[value] if prefer_rank else first[value]
            graph.append(_Node(value, "?", -1, -1, order))
    for value, (operation, left, right) in chosen.items():
        node = graph[locations[value]]
        node.operation, node.left, node.right = operation, locations[left], locations[right]
    roots = {target: locations[target] for target in set(puzzle.targets)
             if target not in puzzle.starting_values}
    return _compile(puzzle, graph, roots)


def _solve_model(puzzle, program, seed, effort):
    # Keep the numerical stack optional for the standard-library package.
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    values, nonseeds, recipes, first, ranks, unrestricted = _problem(puzzle, program, seed, effort)
    if not nonseeds:
        return []
    value_columns = {value: index for index, value in enumerate(nonseeds)}
    recipe_columns = {}
    column = len(nonseeds)
    for value in nonseeds:
        for recipe in recipes[value]:
            recipe_columns[(value, recipe)] = column
            column += 1
    order_columns = {}
    if unrestricted:
        for value in nonseeds:
            order_columns[value] = column
            column += 1
    objective = np.zeros(column)
    objective[:len(nonseeds)] = 1
    integrality = np.ones(column, dtype=np.uint8)
    lower = np.zeros(column)
    upper = np.ones(column)
    for target in set(puzzle.targets) - set(puzzle.starting_values):
        lower[value_columns[target]] = 1
    if unrestricted:
        for position in order_columns.values():
            integrality[position] = 0
            lower[position], upper[position] = 1, len(nonseeds)
    row_indices, column_indices, coefficients = [], [], []
    row_lower, row_upper = [], []

    def constraint(items, low=-np.inf, high=0):
        row = len(row_lower)
        for position, coefficient in items:
            row_indices.append(row)
            column_indices.append(position)
            coefficients.append(coefficient)
        row_lower.append(low)
        row_upper.append(high)

    for value in nonseeds:
        choices = [(recipe_columns[(value, recipe)], 1) for recipe in recipes[value]]
        constraint([(value_columns[value], -1)] + choices, 0, 0)
        for recipe in recipes[value]:
            position = recipe_columns[(value, recipe)]
            for child in set(recipe[1:]):
                if child in value_columns:
                    constraint([(position, 1), (value_columns[child], -1)])
                    if unrestricted:
                        # Selected edges strictly increase a continuous level;
                        # this forbids arithmetic cycles without integral ranks.
                        big_m = len(nonseeds) + 1
                        constraint([(order_columns[child], 1), (order_columns[value], -1),
                                    (position, big_m)], high=big_m - 1)
    matrix = coo_matrix((coefficients, (row_indices, column_indices)),
                        shape=(len(row_lower), column)).tocsc()
    options = {"time_limit": float(min(6, max(1, effort))), "mip_rel_gap": 0.0,
               "presolve": True, "threads": 1, "random_seed": abs(seed) % (2**31 - 1)}
    # SciPy deliberately forwards non-wrapper HiGHS options. Each laboratory
    # worker gets one solver thread, avoiding nested CPU oversubscription.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Unrecognized options detected.*", category=RuntimeWarning)
        result = milp(objective, integrality=integrality, bounds=Bounds(lower, upper),
                      constraints=LinearConstraint(matrix, row_lower, row_upper), options=options)
    if result.x is None:
        return program
    chosen = {}
    for value in nonseeds:
        if result.x[value_columns[value]] < 0.5:
            continue
        options = [recipe for recipe in recipes[value]
                   if result.x[recipe_columns[(value, recipe)]] > 0.5]
        if len(options) != 1:
            return program
        chosen[value] = options[0]
    best = program
    for prefer_rank in (False, True):
        try:
            candidate = _selected_program(puzzle, nonseeds, chosen, first, ranks, prefer_rank)
        except (ValueError, KeyError):
            continue
        if len(candidate) < len(best):
            best = candidate
    return best


def optimize_milp(puzzle: Puzzle, program: list[list], seed=0, effort=1) -> list[list]:
    """Return a validated, non-regressing program from global recipe choice.

    Seeds cycle through height, original, randomized topological, and fully
    unrestricted recipe models. The latter uses continuous levels to forbid
    selected cycles. Seeds >= 4 add a second generation of new helper values,
    with at most 128 new values total. Optional SciPy/HiGHS time is bounded to 1..6 seconds per
    puzzle. Programs that cannot be scheduled in the available registers keep
    their supplied baseline.
    """
    validate_program(puzzle, program)
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    original = [instruction[:] for instruction in program]
    if not original:
        return original
    try:
        candidate = _solve_model(puzzle, original, seed, min(effort, 6))
    except (ImportError, ValueError, KeyError):
        return original
    validate_program(puzzle, candidate)
    return candidate if len(candidate) < len(original) else original
