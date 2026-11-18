// SPDX-License-Identifier: Apache-2.0
// Coordinated value-graph recipe search, developed for this repository.
// Reuses the Apache-2.0 Tony Li / konbu17 Rust graph and 48-register allocator.
// See repair.rs, THIRD_PARTY.md and licenses/Apache-2.0.txt.
// Protocol: id limit ns nt, starts, targets, n_programs, then count/op i j d per program.
// Arguments: seed=0 evaluations=1600 beam=8 exact_values=1024 milliseconds=2000 helpers=0.

#[allow(dead_code)]
mod engine {
    include!("repair.rs");

    use std::time::{Duration, Instant};
    use std::collections::HashSet;

    #[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
    struct Recipe { op: u8, a: usize, b: usize }

    #[derive(Clone)]
    struct JointState {
        choices: Vec<u16>,
        cost: usize,
        active: Vec<usize>,
        hash: u64,
    }

    struct JointGraph {
        values: Vec<Int>,
        recipes: Vec<Vec<Recipe>>,
        seeds: Vec<bool>,
        targets: Vec<usize>,
        sources: Vec<Vec<u16>>,
        helpers: Vec<JointHelper>,
    }

    struct JointHelper { node: usize, consumers: Vec<(usize, Vec<u16>)> }

    struct JointTrace { enabled: bool, helpers: usize, macros: usize, mutations: usize, improvements: usize }

    impl JointTrace {
        fn new() -> Self {
            Self { enabled: env::var("SANTA2026_JOINT_TRACE").map_or(false, |value| value != "0"),
                helpers: 0, macros: 0, mutations: 0, improvements: 0 }
        }
    }

    impl Drop for JointTrace {
        fn drop(&mut self) {
            if self.enabled {
                eprintln!("joint_trace helpers={} macroeval={} mutations={} improvements={}",
                          self.helpers, self.macros, self.mutations, self.improvements);
            }
        }
    }

    type RawRecipe = (u8, Int, Int);

    struct Helper {
        value: Int,
        recipes: BTreeSet<RawRecipe>,
        routes: BTreeMap<Int, BTreeSet<RawRecipe>>,
        earliest: usize,
    }

    fn fresh_helpers(puzzle: &Puzzle, values: &mut BTreeSet<Int>,
                     observed: &mut BTreeMap<Int, BTreeSet<RawRecipe>>,
                     first: &BTreeMap<Int, usize>, maximum: usize, seed: u64, deadline: Instant) {
        if maximum == 0 { return; }
        let known: HashSet<Int> = values.iter().copied().collect();
        let targets: BTreeSet<Int> = puzzle.targets.iter().copied()
            .filter(|value| !puzzle.starts.contains(value)).collect();
        if targets.len() < 2 { return; }
        let mut atoms: Vec<Int> = values.iter().copied().collect();
        atoms.sort_unstable_by_key(|value| (first.get(value).copied().unwrap_or(usize::MAX), *value));
        atoms.truncate(if seed % 2 == 0 { 170 } else { 360 });
        let mut candidates: BTreeMap<Int, Helper> = BTreeMap::new();
        let mut visited = HashSet::new();
        let mut examined = 0usize;
        'pairs: for (position, &a) in atoms.iter().enumerate() {
            for &b in &atoms[..=position] {
                examined += 1;
                if examined & 255 == 0 && Instant::now() >= deadline { break 'pairs; }
                let mut possible = Vec::with_capacity(5);
                if a <= puzzle.limit - b { possible.push((a + b, b'+', a, b)); }
                if a > b { possible.push((a - b, b'-', a, b)); }
                else if b > a { possible.push((b - a, b'-', b, a)); }
                if a <= puzzle.limit / b { possible.push((a * b, b'*', a, b)); }
                if a % b == 0 { possible.push((a / b, b'/', a, b)); }
                if b % a == 0 { possible.push((b / a, b'/', b, a)); }
                for (value, op, mut left, mut right) in possible {
                    if value <= 0 || known.contains(&value) { continue; }
                    if (op == b'+' || op == b'*') && left > right {
                        std::mem::swap(&mut left, &mut right);
                    }
                    let rule = (op, left, right);
                    if let Some(helper) = candidates.get_mut(&value) {
                        if helper.recipes.len() < 12 { helper.recipes.insert(rule); }
                        helper.earliest = helper.earliest.min(first[&a].max(first[&b]));
                        continue;
                    }
                    if !visited.insert(value) { continue; }
                    let mut routes: BTreeMap<Int, BTreeSet<RawRecipe>> = BTreeMap::new();
                    for &target in &targets {
                        let mut offer = |operation: u8, other: Int, helper_left: bool| {
                            if other != target && known.contains(&other) {
                                let (mut x, mut y) = if helper_left { (value, other) } else { (other, value) };
                                if (operation == b'+' || operation == b'*') && x > y { std::mem::swap(&mut x, &mut y); }
                                routes.entry(target).or_default().insert((operation, x, y));
                            }
                        };
                        if target > value { offer(b'+', target - value, true); }
                        if value > target { offer(b'-', value - target, true); }
                        if target <= puzzle.limit - value { offer(b'-', target + value, false); }
                        if target % value == 0 { offer(b'*', target / value, true); }
                        if value % target == 0 { offer(b'/', value / target, true); }
                        if value <= puzzle.limit / target { offer(b'/', target * value, false); }
                    }
                    if routes.len() >= 2 {
                        candidates.insert(value, Helper { value, recipes: BTreeSet::from([rule]), routes,
                            earliest: first[&a].max(first[&b]) });
                    }
                }
            }
        }
        let mut candidates: Vec<Helper> = candidates.into_values().collect();
        candidates.sort_by_key(|helper| (std::cmp::Reverse(helper.routes.len()), helper.earliest, helper.value));
        let mut added_routes: BTreeMap<Int, usize> = BTreeMap::new();
        for helper in candidates.into_iter().take(maximum) {
            values.insert(helper.value);
            observed.entry(helper.value).or_default().extend(helper.recipes.into_iter().take(12));
            for (target, routes) in helper.routes {
                for route in routes {
                    let count = added_routes.entry(target).or_default();
                    if *count >= 12 { break; }
                    if observed.entry(target).or_default().insert(route) { *count += 1; }
                }
            }
        }
    }

    fn canonical(op: u8, a: usize, b: usize) -> Recipe {
        if (op == b'+' || op == b'*') && a > b {
            Recipe { op, a: b, b: a }
        } else { Recipe { op, a, b } }
    }

    fn graph_hash(choices: &[u16]) -> u64 {
        choices.iter().fold(0xcbf29ce484222325u64, |hash, &item| {
            (hash ^ u64::from(item)).wrapping_mul(0x100000001b3)
        })
    }

    fn collect_graph(puzzle: &Puzzle, programs: &[Program], exact_values: usize,
                     helpers: usize, seed: u64, deadline: Instant,
                     validated_best: &mut Option<Program>) -> Outcome<Option<JointGraph>> {
        if puzzle.starts.is_empty() || puzzle.starts.len() > 48 ||
            puzzle.starts.iter().any(|&value| value <= 0 || value > puzzle.limit) {
            return Err("invalid joint starting values".into());
        }
        let initial: BTreeSet<Int> = puzzle.starts.iter().copied().collect();
        let mut values = initial.clone();
        let mut observed: BTreeMap<Int, BTreeSet<(u8, Int, Int)>> = BTreeMap::new();
        let mut first: BTreeMap<Int, usize> = puzzle.starts.iter().enumerate().map(|(i, &v)| (v, i)).collect();
        let mut source_rules = Vec::new();
        let mut ordered: Vec<&Program> = programs.iter().collect();
        ordered.sort_by_key(|hint| hint.len());
        for hint in ordered {
            if validated_best.is_some() && Instant::now() >= deadline { return Ok(None); }
            if hint.len() > 4096 { return Err("invalid joint hint length".into()); }
            let mut bank = [0; 48];
            bank[..puzzle.starts.len()].copy_from_slice(&puzzle.starts);
            let mut covered: HashSet<Int> = initial.iter().copied().collect();
            let mut source = BTreeMap::new();
            for (position, &[op, left, right, destination]) in hint.iter().enumerate() {
                // Finish one shortest hint's integer replay even if its tiny
                // validation overhead exhausts the budget: an unvalidated
                // input must never be emitted as a timeout fallback.
                if position & 63 == 0 && validated_best.is_some() && Instant::now() >= deadline { return Ok(None); }
                if !(0..48).contains(&left) || !(0..48).contains(&right) || !(0..48).contains(&destination) {
                    return Err("invalid joint hint register".into());
                }
                let (mut a, mut b) = (bank[left as usize], bank[right as usize]);
                if a == 0 || b == 0 { return Err("undefined joint hint source".into()); }
                if ![b'+', b'-', b'*', b'/'].contains(&(op as u8)) ||
                    (op as u8 == b'-' && a <= b) || (op as u8 == b'/' && a % b != 0) ||
                    (op as u8 == b'*' && a > puzzle.limit / b) { return Err("illegal joint hint arithmetic".into()); }
                let value = apply(op as u8, a, b)?;
                if value <= 0 || value > puzzle.limit { return Err("joint hint exceeds value limit".into()); }
                values.insert(value);
                first.entry(value).and_modify(|old| *old = (*old).min(position + puzzle.starts.len()))
                    .or_insert(position + puzzle.starts.len());
                if !initial.contains(&value) && value != a && value != b {
                    if (op as u8 == b'+' || op as u8 == b'*') && a > b {
                        std::mem::swap(&mut a, &mut b);
                    }
                    let recipe = (op as u8, a, b);
                    observed.entry(value).or_default().insert(recipe);
                    source.entry(value).or_insert(recipe);
                }
                bank[destination as usize] = value;
                covered.insert(value);
            }
            if puzzle.targets.iter().any(|value| !covered.contains(value)) { return Err("joint hint misses targets".into()); }
            if validated_best.as_ref().map_or(true, |best| hint.len() < best.len()) { *validated_best = Some(hint.clone()); }
            if Instant::now() >= deadline { return Ok(None); }
            source_rules.push(source);
        }
        let witnessed_sizes: HashMap<Int, usize> = observed.iter().map(|(&value, rules)| (value, rules.len())).collect();
        fresh_helpers(puzzle, &mut values, &mut observed, &first, helpers, seed, deadline);
        if Instant::now() >= deadline { return Ok(None); }
        let values: Vec<Int> = values.into_iter().collect();
        if values.len() > 65_536 { return Err("joint graph exceeds value budget".into()); }
        let locations: HashMap<Int, usize> = values.iter().enumerate().map(|(i, &v)| (v, i)).collect();
        let seeds: Vec<bool> = values.iter().map(|v| initial.contains(v)).collect();
        let recipe_caps: Vec<usize> = values.iter().map(|value| {
            if helpers == 0 { 4096 } else { witnessed_sizes.get(value).copied().unwrap_or(0) + 12 }
        }).collect();
        let mut recipes: Vec<BTreeSet<Recipe>> = values.iter().map(|_| BTreeSet::new()).collect();
        for (position, (&value, alternatives)) in observed.iter().enumerate() {
            if position & 63 == 0 && Instant::now() >= deadline { return Ok(None); }
            let index = locations[&value];
            for &(op, a, b) in alternatives {
                recipes[index].insert(canonical(op, locations[&a], locations[&b]));
            }
        }
        // Enumerate exact one-operation alternatives only inside the known
        // value universe. No intermediate or constant is treated as free.
        // Bound the quadratic join; keep every observed recipe regardless.
        let mut atoms = BTreeSet::new();
        for &value in &puzzle.starts { atoms.insert(locations[&value]); }
        for &value in &puzzle.targets { atoms.insert(locations[&value]); }
        for source in &source_rules {
            for (&value, &(_, a, b)) in source {
                if atoms.len() >= exact_values { break; }
                atoms.insert(locations[&value]); atoms.insert(locations[&a]); atoms.insert(locations[&b]);
            }
        }
        for index in 0..values.len() {
            if atoms.len() >= exact_values { break; }
            atoms.insert(index);
        }
        let atoms: Vec<usize> = atoms.into_iter().take(exact_values).collect();
        let mut pairs = 0usize;
        for (position, &a) in atoms.iter().enumerate() {
            for &b in &atoms[..=position] {
                pairs += 1;
                if pairs & 255 == 0 && Instant::now() >= deadline { return Ok(None); }
                let (av, bv) = (values[a], values[b]);
                let mut offer = |op: u8, result: Int, left: usize, right: usize| {
                    if let Some(&index) = locations.get(&result) {
                        if !seeds[index] && index != left && index != right && recipes[index].len() < recipe_caps[index] {
                            recipes[index].insert(canonical(op, left, right));
                        }
                    }
                };
                if av <= puzzle.limit - bv { offer(b'+', av + bv, a, b); }
                if av > bv { offer(b'-', av - bv, a, b); }
                if av <= puzzle.limit / bv { offer(b'*', av * bv, a, b); }
                if av % bv == 0 { offer(b'/', av / bv, a, b); }
                if bv % av == 0 { offer(b'/', bv / av, b, a); }
            }
        }
        let recipes: Vec<Vec<Recipe>> = recipes.into_iter().map(|set| set.into_iter().collect()).collect();
        let mut sources = Vec::new();
        for source in source_rules {
            if Instant::now() >= deadline { return Ok(None); }
            let mut choices = vec![0; values.len()];
            for (value, (op, a, b)) in source {
                let index = locations[&value];
                let recipe = canonical(op, locations[&a], locations[&b]);
                choices[index] = recipes[index].binary_search(&recipe).map_err(|_| "lost source recipe")? as u16;
            }
            sources.push(choices);
        }
        let targets = puzzle.targets.iter().map(|v| locations[v]).collect();
        let mut macro_helpers = Vec::new();
        if helpers > 0 {
            for (index, &value) in values.iter().enumerate() {
                if first.contains_key(&value) { continue; }
                let mut consumers = Vec::new();
                for &target in &puzzle.targets {
                    let node = locations[&target];
                    let choices: Vec<u16> = recipes[node].iter().enumerate()
                        .filter(|(_, recipe)| recipe.a == index || recipe.b == index)
                        .map(|(i, _)| i as u16).collect();
                    if !choices.is_empty() { consumers.push((node, choices)); }
                }
                consumers.sort_by_key(|item| item.0); consumers.dedup_by_key(|item| item.0);
                if consumers.len() >= 2 { macro_helpers.push(JointHelper { node: index, consumers }); }
            }
        }
        Ok(Some(JointGraph { values, recipes, seeds, targets, sources, helpers: macro_helpers }))
    }

    fn topology(graph: &JointGraph, choices: &[u16], deadline: Instant) -> Option<Vec<usize>> {
        let mut colors = vec![0u8; graph.values.len()];
        let mut ordered = Vec::new();
        let mut steps = 0usize;
        for &target in &graph.targets {
            let mut stack = vec![(target, false)];
            while let Some((value, leaving)) = stack.pop() {
                steps += 1;
                if steps & 255 == 0 && Instant::now() >= deadline { return None; }
                if graph.seeds[value] { colors[value] = 2; continue; }
                if leaving {
                    colors[value] = 2; ordered.push(value); continue;
                }
                if colors[value] == 2 { continue; }
                if colors[value] == 1 { return None; }
                let recipe = graph.recipes[value].get(choices[value] as usize)?;
                colors[value] = 1;
                stack.push((value, true));
                stack.push((recipe.b, false)); stack.push((recipe.a, false));
            }
        }
        Some(ordered)
    }

    fn evaluate(puzzle: &Puzzle, graph: &JointGraph, choices: Vec<u16>, best: &mut Program,
                seen: &mut BTreeSet<u64>, deadline: Instant) -> Option<JointState> {
        if Instant::now() >= deadline { return None; }
        let hash = graph_hash(&choices);
        if !seen.insert(hash) { return None; }
        let active = topology(graph, &choices, deadline)?;
        // A count of distinct nodes is only a lower bound. Keep a small uphill
        // allowance for coordinated moves, then score the actual executable.
        if active.len() > best.len() + 12 { return None; }
        let mut candidate = puzzle.clone();
        candidate.arguments.clear(); candidate.definitions.clear(); candidate.order.clear();
        for &value in &candidate.starts {
            candidate.definitions.insert(value, Expr { value, a: -1, b: -1, op: 0, cost: 0 });
        }
        for (position, &index) in active.iter().enumerate() {
            if position & 127 == 0 && Instant::now() >= deadline { return None; }
            let recipe = graph.recipes[index][choices[index] as usize];
            let value = graph.values[index];
            candidate.definitions.insert(value, Expr { value, a: -1, b: -1, op: recipe.op, cost: 0 });
            candidate.arguments.insert(value, (graph.values[recipe.a], graph.values[recipe.b]));
            candidate.order.push(value);
        }
        if Instant::now() >= deadline { return None; }
        let program = candidate.compile().ok()?;
        if program.len() > 4096 { return None; }
        let cost = program.len();
        if cost < best.len() {
            // Independent replay through the importer's integer checks before
            // replacing the accepted shortest validated hint.
            let mut replay = puzzle.clone();
            replay.load_hint(&program).ok()?;
            *best = program;
        }
        Some(JointState { choices, cost, active, hash })
    }

    fn insert_beam(beam: &mut Vec<JointState>, state: JointState, width: usize) {
        if beam.iter().any(|old| old.hash == state.hash) { return; }
        let position = beam.partition_point(|old| (old.cost, old.active.len(), old.hash) <= (state.cost, state.active.len(), state.hash));
        beam.insert(position, state);
        if beam.len() > width { beam.pop(); }
    }

    fn exclusive_costs(graph: &JointGraph, parent: &JointState) -> Vec<usize> {
        let mut uses = vec![0usize; graph.values.len()];
        for &target in &graph.targets { uses[target] += 1; }
        for &node in &parent.active {
            let recipe = graph.recipes[node][parent.choices[node] as usize];
            uses[recipe.a] += 1;
            if recipe.a != recipe.b { uses[recipe.b] += 1; }
        }
        let mut costs = vec![0usize; graph.values.len()];
        for &node in &parent.active {
            let recipe = graph.recipes[node][parent.choices[node] as usize];
            costs[node] = 1;
            if uses[recipe.a] == 1 { costs[node] += costs[recipe.a]; }
            if recipe.a != recipe.b && uses[recipe.b] == 1 { costs[node] += costs[recipe.b]; }
        }
        costs
    }

    fn helper_neighborhood(puzzle: &Puzzle, graph: &JointGraph, best: &mut Program,
                           beam: &mut Vec<JointState>, seen: &mut BTreeSet<u64>, width: usize,
                           budget: usize, deadline: Instant, trace: &mut JointTrace) -> usize {
        if graph.helpers.is_empty() || beam.is_empty() || budget == 0 { return 0; }
        let costs = exclusive_costs(graph, &beam[0]);
        let mut helpers: Vec<&JointHelper> = graph.helpers.iter().collect();
        helpers.sort_by_key(|helper| (std::cmp::Reverse(helper.consumers.iter().map(|(node, _)| costs[*node]).sum::<usize>()),
                                    std::cmp::Reverse(helper.consumers.len()), helper.node));
        let mut used = 0usize;
        // Round-robin definition/route alternatives across helpers, so one
        // high-fanout helper cannot consume the entire macro budget.
        for variant in 0..12 {
            for helper in &helpers {
                if used >= budget || Instant::now() >= deadline { return used; }
                let parent = beam[0].clone();
                let definitions = &graph.recipes[helper.node];
                let definition = variant % definitions.len();
                let route_variant = variant / definitions.len();
                let mut consumers = helper.consumers.clone();
                consumers.sort_by_key(|(node, _)| (std::cmp::Reverse(costs[*node]), *node));
                let all: Vec<(usize, u16)> = consumers.iter().map(|(node, routes)| (*node, routes[route_variant % routes.len()])).collect();
                let mut subsets = vec![all.clone()];
                for length in [2, 3] {
                    if length < all.len() { subsets.push(all[..length].to_vec()); }
                }
                for first in 0..all.len().min(4) {
                    for second in first + 1..all.len().min(4) {
                        let pair = vec![all[first], all[second]];
                        if !subsets.contains(&pair) { subsets.push(pair); }
                    }
                }
                for subset in subsets {
                    if used >= budget || Instant::now() >= deadline { return used; }
                    let mut choices = parent.choices.clone();
                    choices[helper.node] = definition as u16;
                    for (node, route) in subset { choices[node] = route; }
                    let previous = best.len();
                    used += 1; trace.macros += 1;
                    if let Some(state) = evaluate(puzzle, graph, choices, best, seen, deadline) {
                        insert_beam(beam, state, width);
                    }
                    if best.len() < previous { trace.improvements += 1; }
                }
            }
        }
        used
    }

    fn optimize(puzzle: &Puzzle, programs: &[Program], seed: u64, evaluations: usize,
                width: usize, exact_values: usize, milliseconds: u64, helpers: usize) -> Outcome<Program> {
        let deadline = Instant::now() + Duration::from_millis(milliseconds);
        let mut trace = JointTrace::new();
        if programs.is_empty() { return Err("no hint programs".into()); }
        let mut validated_best = None;
        let graph = collect_graph(puzzle, programs, exact_values, helpers, seed, deadline, &mut validated_best)?;
        let mut best = validated_best.ok_or("no validated joint hint")?;
        let graph = match graph { Some(graph) => graph, None => return Ok(best) };
        trace.helpers = graph.helpers.len();
        let mut seen = BTreeSet::new();
        let mut beam = Vec::new();
        for choices in &graph.sources {
            if Instant::now() >= deadline { return Ok(best); }
            let previous = best.len();
            if let Some(state) = evaluate(puzzle, &graph, choices.clone(), &mut best, &mut seen, deadline) {
                insert_beam(&mut beam, state, width);
            }
            if best.len() < previous { trace.improvements += 1; }
        }
        if beam.is_empty() { return Ok(best); }
        let mutable: Vec<usize> = graph.recipes.iter().enumerate().filter(|(_, r)| r.len() > 1).map(|(i, _)| i).collect();
        if mutable.is_empty() { return Ok(best); }
        let mut rng = Mt19937_64::new(seed);
        let mut used = helper_neighborhood(puzzle, &graph, &mut best, &mut beam, &mut seen, width,
                                          evaluations.min(512), deadline, &mut trace);
        let mut generation = 0;
        while used < evaluations && Instant::now() < deadline {
            let parents = beam.clone();
            let mut accepted = 0;
            for parent in &parents {
                if Instant::now() >= deadline { return Ok(best); }
                let mut moves = Vec::new();
                for &node in &parent.active {
                    for choice in 0..graph.recipes[node].len() {
                        if choice & 255 == 0 && Instant::now() >= deadline { return Ok(best); }
                        if choice != parent.choices[node] as usize { moves.push((node, choice)); }
                    }
                }
                rng.shuffle(&mut moves);
                // Half the neighborhood is coordinated changes. A source
                // crossover can replace several mutually dependent recipes
                // even when neither individual switch is acyclic or shorter.
                let count = 64usize.min(evaluations - used);
                for step in 0..count {
                    if used >= evaluations || Instant::now() >= deadline { break; }
                    used += 1; trace.mutations += 1;
                    let mut choices = parent.choices.clone();
                    if step < 24 && !moves.is_empty() {
                        let (node, choice) = moves[(generation * 24 + step) % moves.len()];
                        choices[node] = choice as u16;
                    } else {
                        let switches = 2 + usize::from(rng.next() & 1 != 0);
                        let source = &graph.sources[(rng.next() as usize) % graph.sources.len()];
                        let crossing = rng.next() & 1 != 0;
                        for _ in 0..switches {
                            let active_choice = rng.next() & 3 != 0 && !moves.is_empty();
                            let node = if active_choice { moves[(rng.next() as usize) % moves.len()].0 }
                                       else { mutable[(rng.next() as usize) % mutable.len()] };
                            let alternatives = graph.recipes[node].len();
                            if crossing && source[node] != choices[node] { choices[node] = source[node]; }
                            else {
                                let offset = 1 + (rng.next() as usize) % (alternatives - 1);
                                choices[node] = ((choices[node] as usize + offset) % alternatives) as u16;
                            }
                        }
                    }
                    let previous = best.len();
                    if let Some(state) = evaluate(puzzle, &graph, choices, &mut best, &mut seen, deadline) {
                        insert_beam(&mut beam, state, width);
                        accepted += 1;
                    }
                    if best.len() < previous { trace.improvements += 1; }
                }
            }
            generation += 1;
            if accepted == 0 && generation > 4 { break; }
        }
        Ok(best)
    }

    #[cfg(test)]
    mod joint_deadline_tests {
        use super::*;

        #[test]
        fn expired_deadline_returns_a_fully_validated_shortest_hint() {
            let puzzle = Puzzle { limit: 10, starts: vec![3], targets: vec![1],
                definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            let shortest = vec![[b'/' as i32, 0, 0, 1]];
            let longer = vec![[b'/' as i32, 0, 0, 1], [b'+' as i32, 1, 1, 2]];
            let mut fallback = None;
            let graph = collect_graph(&puzzle, &[longer, shortest.clone()], 4096, 24, 0,
                                      Instant::now() - Duration::from_secs(1), &mut fallback).unwrap();
            assert!(graph.is_none());
            assert_eq!(fallback, Some(shortest));
            let mut replay = puzzle.clone();
            replay.load_hint(&fallback.unwrap()).unwrap();
        }

        #[test]
        fn expired_deadline_does_not_bypass_first_hint_validation() {
            let puzzle = Puzzle { limit: 10, starts: vec![3, 2], targets: vec![1],
                definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            let mut fallback = None;
            assert!(collect_graph(&puzzle, &[vec![[b'/' as i32, 0, 1, 2]]], 4096, 24, 0,
                                 Instant::now() - Duration::from_secs(1), &mut fallback).is_err());
            assert!(fallback.is_none());
        }
    }

    pub fn run_joint() -> Outcome<()> {
        fn parameter<T: std::str::FromStr>(args: &[String], i: usize, default: T) -> Outcome<T> {
            args.get(i).map_or(Ok(default), |value| value.parse().map_err(|_| "invalid joint argument".into()))
        }
        let args: Vec<String> = env::args().collect();
        let seed = parameter(&args, 1, 0u64)?;
        let evaluations = parameter(&args, 2, 1600usize)?;
        let width = parameter(&args, 3, 8usize)?;
        let exact_values = parameter(&args, 4, 1024usize)?;
        let milliseconds = parameter(&args, 5, 2000u64)?;
        let helpers = parameter(&args, 6, 0usize)?;
        if evaluations > 1_000_000 || width == 0 || width > 64 || exact_values > 4096 || milliseconds == 0 || milliseconds > 120_000 || helpers > 24 {
            return Err("invalid joint search budget".into());
        }
        let stdin = io::stdin(); let stdout = io::stdout();
        let mut scanner = Scanner::new(BufReader::new(stdin.lock()));
        let mut writer = BufWriter::new(stdout.lock());
        while let Some(id) = scanner.next()? {
            let limit: Int = scanner.integer()?;
            let ns: usize = scanner.integer()?; let nt: usize = scanner.integer()?;
            if !(1..=1_000_000_000_000_000_000).contains(&limit) || ns == 0 || ns > 48 || nt > 4096 {
                return Err("invalid joint puzzle dimensions".into());
            }
            let mut starts = Vec::new(); let mut targets = Vec::new();
            for _ in 0..ns { starts.push(scanner.integer()?); }
            for _ in 0..nt { targets.push(scanner.integer()?); }
            let count: usize = scanner.integer()?;
            if count == 0 || count > 64 { return Err("invalid number of joint hints".into()); }
            let mut programs = Vec::new();
            for _ in 0..count {
                let length: usize = scanner.integer()?;
                if length > 4096 { return Err("invalid joint hint length".into()); }
                let mut program = Vec::new();
                for _ in 0..length {
                    let op = scanner.next()?.ok_or("truncated joint hint")?;
                    if op.len() != 1 { return Err("invalid joint operator".into()); }
                    program.push([op.as_bytes()[0] as i32, scanner.integer()?, scanner.integer()?, scanner.integer()?]);
                }
                programs.push(program);
            }
            let puzzle = Puzzle { limit, starts, targets, definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            let best = optimize(&puzzle, &programs, seed, evaluations, width, exact_values, milliseconds, helpers)?;
            write!(writer, "{} [", id).map_err(|error| error.to_string())?;
            for (index, instruction) in best.iter().enumerate() {
                if index > 0 { write!(writer, ",").map_err(|error| error.to_string())?; }
                write!(writer, "[\"{}\",{},{},{}]", instruction[0] as u8 as char, instruction[1], instruction[2], instruction[3]).map_err(|error| error.to_string())?;
            }
            writeln!(writer, "]").map_err(|error| error.to_string())?;
            writer.flush().map_err(|error| error.to_string())?;
        }
        Ok(())
    }
}

fn main() {
    if let Err(error) = engine::run_joint() {
        eprintln!("Joint search error: {}", error);
        std::process::exit(1);
    }
}
