"""Recombine several arithmetic programs with an acyclic CP-SAT value graph.

By default only recipes observed in supplied, validated programs are used. The model may
mix their derivations, but every chosen dependency must increase an integer
topological rank. The selected graph is allocated to the real register bank
and replayed before it can replace the shortest input program. OR-Tools is an
optional laboratory dependency; the core package remains importable without it.
"""

from __future__ import annotations

import random
from collections import defaultdict

from .core import Puzzle, validate_program
from .optimizer import _Node, _compile, _lift, _reachable


def _recipe(operation, left, right):
    if operation in ("+", "*") and left > right:
        left, right = right, left
    return operation, left, right


def _union(puzzle, programs):
    """Collect live value identities and alternative recipes from every input."""
    seeds = set(puzzle.starting_values)
    recipes = defaultdict(set)
    first = {}
    warm = {}
    warm_levels = {}
    for number, program in enumerate(programs):
        nodes, roots, _ = _lift(puzzle, program)
        active = _reachable(nodes, roots.values())
        for index in sorted(active):
            node = nodes[index]
            if node.operation is None or node.value in seeds:
                continue
            left, right = nodes[node.left].value, nodes[node.right].value
            # Identity recipes have no useful value-graph contribution and
            # would otherwise create a self-edge after canonicalizing values.
            if node.value in (left, right):
                continue
            rule = _recipe(node.operation, left, right)
            recipes[node.value].add(rule)
            first[node.value] = min(first.get(node.value, node.order), node.order)
            if number == 0 and node.value not in warm:
                warm[node.value] = rule
                warm_levels[node.value] = 1 + max(warm_levels.get(left, 0), warm_levels.get(right, 0))
    return {value: sorted(choices) for value, choices in sorted(recipes.items())}, first, warm, warm_levels


def _cross_recipes(puzzle, recipes):
    """Connect pooled values through every legal one-operation identity.

    This adds alternative edges, not free values: the model still pays for
    every selected nonseed value and enforces an acyclic construction.
    """
    choices = {value: set(rules) for value, rules in recipes.items()}
    values = sorted(set(recipes) | set(puzzle.starting_values))

    def offer(result, operation, left, right):
        if result in choices and result not in (left, right):
            choices[result].add(_recipe(operation, left, right))

    for index, left in enumerate(values):
        for right in values[index:]:
            offer(left + right, "+", left, right)
            if left <= puzzle.value_limit // right:
                offer(left * right, "*", left, right)
            if right > left:
                offer(right - left, "-", right, left)
            if right % left == 0:
                offer(right // left, "/", right, left)
    return {value: sorted(rules) for value, rules in choices.items()}


def _make_model(puzzle, recipes, warm, warm_levels, cp_model):
    """Build a minimum-value acyclic AND/OR recipe selection model."""
    model = cp_model.CpModel()
    selected = {value: model.new_bool_var(f"value_{index}") for index, value in enumerate(recipes)}
    levels = {value: model.new_int_var(1, len(recipes), f"rank_{index}")
              for index, value in enumerate(recipes)}
    recipe_variables = {}
    for result, alternatives in recipes.items():
        choices = []
        for number, rule in enumerate(alternatives):
            variable = model.new_bool_var(f"recipe_{result}_{number}")
            recipe_variables[result, rule] = variable
            choices.append(variable)
            for operand in set(rule[1:]):
                if operand in selected:
                    model.add_implication(variable, selected[operand])
                    model.add(levels[result] >= levels[operand] + 1).only_enforce_if(variable)
        model.add(sum(choices) == selected[result])
    for target in set(puzzle.targets) - set(puzzle.starting_values):
        if target not in selected:
            raise ValueError("recombination graph misses a required target")
        model.add(selected[target] == 1)
    model.minimize(sum(selected.values()))
    for value, variable in selected.items():
        model.add_hint(variable, int(value in warm))
        model.add_hint(levels[value], warm_levels.get(value, 1))
    for (value, rule), variable in recipe_variables.items():
        model.add_hint(variable, int(warm.get(value) == rule))
    return model, selected, recipe_variables, levels


def _compile_selection(puzzle, chosen, first, levels, seed):
    """Try several dependency schedules before rejecting register pressure."""
    seeds = set(puzzle.starting_values)
    targets = sorted(set(puzzle.targets) - seeds)
    root_orders = [targets, list(reversed(targets)),
                   sorted(targets, key=lambda value: (first.get(value, 0), value))]
    shuffled = list(targets)
    random.Random(seed).shuffle(shuffled)
    root_orders.append(shuffled)
    priorities = [first, levels]
    for roots in root_orders:
        for reverse_children in (False, True):
            seen = set(seeds)
            order = {}

            def visit(value):
                if value in seen:
                    return
                seen.add(value)
                _, left, right = chosen[value]
                children = (right, left) if reverse_children else (left, right)
                for child in children:
                    visit(child)
                order[value] = len(order)

            for target in roots:
                visit(target)
            priorities.append(order)
    best = None
    for priority in priorities:
        graph = [_Node(value, None, -1, -1, -1) for value in puzzle.starting_values]
        locations = {}
        for index, value in enumerate(puzzle.starting_values):
            locations.setdefault(value, index)
        for value in chosen:
            locations[value] = len(graph)
            graph.append(_Node(value, "?", -1, -1, int(priority.get(value, first.get(value, 0)))))
        for value, (operation, left, right) in chosen.items():
            node = graph[locations[value]]
            node.operation, node.left, node.right = operation, locations[left], locations[right]
        roots = {target: locations[target] for target in targets}
        try:
            candidate = _compile(puzzle, graph, roots)
        except (ValueError, KeyError):
            continue
        if best is None or len(candidate) < len(best):
            best = candidate
    return best


def optimize_recombine(puzzle: Puzzle, programs: list[list[list]], seed=0, effort=1, cross_recipes=False) -> list[list]:
    """Return a validated program no longer than any supplied candidate.

    Inputs are never modified. The compiled CP-SAT search uses one worker and
    at most 2..12 seconds, depending on effort. Missing optional OR-Tools,
    absence of a feasible incumbent, or excess physical register pressure all
    retain a copy of the shortest valid input program.
    """
    if type(seed) is not int or type(effort) is not int or effort < 1:
        raise ValueError("seed and positive effort must be integers")
    if not isinstance(programs, (list, tuple)) or not programs:
        raise ValueError("at least one candidate program is required")
    for program in programs:
        validate_program(puzzle, program)
    ordered = sorted(programs, key=len)
    original = [instruction[:] for instruction in ordered[0]]
    if not set(puzzle.targets) - set(puzzle.starting_values):
        return []
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return original
    recipes, first, warm, warm_levels = _union(puzzle, ordered)
    if cross_recipes:
        recipes = _cross_recipes(puzzle, recipes)
    model, selected, recipe_variables, ranks = _make_model(puzzle, recipes, warm, warm_levels, cp_model)
    # The input program always gives a valid upper bound. Its canonical graph
    # may be cheaper still after repeated values and dead instructions vanish.
    model.add(sum(selected.values()) <= len(original))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(min(12, 2 * effort))
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = abs(seed) % (2**31 - 1)
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return original
    chosen = {}
    for value, variable in selected.items():
        if not solver.boolean_value(variable):
            continue
        alternatives = [rule for rule in recipes[value]
                        if solver.boolean_value(recipe_variables[value, rule])]
        if len(alternatives) != 1:
            return original
        chosen[value] = alternatives[0]
    levels = {value: solver.value(ranks[value]) for value in chosen}
    try:
        candidate = _compile_selection(puzzle, chosen, first, levels, seed)
    except (ValueError, KeyError, RecursionError):
        return original
    if candidate is None:
        return original
    validate_program(puzzle, candidate)
    return candidate if len(candidate) < len(original) else original


def optimize_recombine_cross(puzzle: Puzzle, programs: list[list[list]], seed=0, effort=1) -> list[list]:
    """Recombine a Rust trajectory, including new edges between pooled values."""
    return optimize_recombine(puzzle, programs, seed=seed, effort=effort, cross_recipes=True)
