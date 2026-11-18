// SPDX-License-Identifier: Apache-2.0
// Joint tail reconstruction with a beam of value graphs and one-step lookahead.
// Reuses the Apache-2.0 dictionary/search/allocator port in repair.rs, derived
// from Tony Li and konbu17; see that file, THIRD_PARTY.md and the license text.

#[allow(dead_code)]
mod engine {
    include!("repair.rs");

    use std::time::{Duration, Instant};

    const GRAPH_WIDTH: usize = 3;
    const SHORTLIST: usize = 6;

    struct Budget {
        started: Instant,
        duration: Duration,
        searches: usize,
        maximum_searches: usize,
        neighborhoods: usize,
        accepted: usize,
        completed: usize,
        boundary_first: bool,
    }

    impl Budget {
        fn new(config: &Config) -> Outcome<Self> {
            let default_ms = config.trials.max(1).saturating_mul(config.passes).saturating_mul(400)
                .clamp(1000, 30_000);
            let milliseconds: u64 = match env::var("SANTA2026_OPTIMIZER_MS") {
                Ok(value) => value.parse().map_err(|_| "invalid optimizer time budget".to_string())?,
                Err(_) => default_ms as u64,
            };
            if milliseconds == 0 || milliseconds > 600_000 {
                return Err("optimizer time budget must be within 1..600000 milliseconds".into());
            }
            let boundary_first = match env::var("SANTA2026_OPTIMIZER_BOUNDARY").as_deref() {
                Ok("1") => true,
                Ok("0") | Err(_) => false,
                _ => return Err("optimizer boundary option must be 0 or 1".into()),
            };
            Ok(Self { started: Instant::now(), duration: Duration::from_millis(milliseconds),
                searches: 0, maximum_searches: config.trials.max(1).saturating_mul(config.passes)
                    .saturating_mul(512).min(100_000), neighborhoods: 0, accepted: 0, completed: 0,
                boundary_first })
        }
        fn available(&self) -> bool {
            self.searches < self.maximum_searches && self.started.elapsed() < self.duration
        }
        fn search<'a>(&mut self, dictionary: &'a Dictionary, config: &Config, target: Int) -> Option<Search<'a>> {
            if !self.available() { return None; }
            self.searches += 1;
            let mut search = Search::new(dictionary, config);
            // Same backward beam as repair.rs, with cancellation between parent
            // expansions. An unfinished search is discarded, never emitted.
            search.history.push(State { value: target, cost: 0, parent: -1,
                link: Link::default(), rank: 0.0 });
            let id = dictionary.index.get(target);
            if id >= 0 {
                search.end = 0;
                search.terminal = id;
                search.best = dictionary.expressions[id as usize].cost;
                return Some(search);
            }
            let mut front = vec![0usize];
            for _ in 0..20 {
                if front.is_empty() { break; }
                search.next.clear();
                for &parent in &front {
                    if self.started.elapsed() >= self.duration { return None; }
                    if search.history[parent].cost + 3 < search.best { search.expand(parent); }
                }
                front.clear();
                for state in &search.next {
                    if state.cost + 3 >= search.best { continue; }
                    search.visited.insert(state.value, state.cost);
                    front.push(search.history.len());
                    search.history.push(*state);
                }
            }
            Some(search)
        }
    }

    #[derive(Clone)]
    struct GraphState {
        puzzle: Puzzle,
        pending: Vec<Int>,
        prefix_cost: usize,
        rank: f64,
        boundary_first: bool,
        boundary: Vec<Int>,
        original_recipes: BTreeMap<Int, (u8, Int, Int)>,
    }

    fn active_values(puzzle: &Puzzle) -> Outcome<BTreeSet<Int>> {
        fn visit(puzzle: &Puzzle, value: Int, active: &mut BTreeSet<Int>) -> Outcome<()> {
            if !active.insert(value) { return Ok(()); }
            if !puzzle.definitions.contains_key(&value) { return Err("missing active value".into()); }
            if let Some(&(a, b)) = puzzle.arguments.get(&value) {
                visit(puzzle, a, active)?;
                visit(puzzle, b, active)?;
            }
            Ok(())
        }
        let mut active = BTreeSet::new();
        for &target in &puzzle.targets { visit(puzzle, target, &mut active)?; }
        Ok(active)
    }

    fn arithmetic_count(puzzle: &Puzzle) -> Outcome<usize> {
        Ok(active_values(puzzle)?.iter().filter(|value| puzzle.arguments.contains_key(value)).count())
    }

    // Exact structural keys keep the graph beam diverse without hash collisions.
    // Target order is included because it changes the physical allocation cost.
    fn graph_key(state: &GraphState) -> Outcome<(Vec<(Int, u8, Int, Int)>, Vec<Int>)> {
        let mut recipes = Vec::new();
        for value in active_values(&state.puzzle)? {
            if let Some(&(a, b)) = state.puzzle.arguments.get(&value) {
                recipes.push((value, state.puzzle.definitions[&value].op, a, b));
            }
        }
        Ok((recipes, state.puzzle.targets.clone()))
    }

    fn completion_order(puzzle: &Puzzle) -> Vec<Int> {
        let required: BTreeSet<Int> = puzzle.targets.iter().copied().collect();
        let mut seen = BTreeSet::new();
        puzzle.starts.iter().chain(&puzzle.order).chain(&puzzle.targets)
            .filter(|value| required.contains(value) && seen.insert(**value)).copied().collect()
    }

    fn tail_state(puzzle: &Puzzle, completion: &[Int], length: usize, boundary_first: bool) -> Outcome<GraphState> {
        let split = completion.len().saturating_sub(length);
        let mut retained = puzzle.clone();
        retained.targets = completion[..split].to_vec();
        let mut outside = active_values(&retained)?;
        outside.extend(retained.starts.iter().copied());
        let mut boundary = BTreeSet::new();
        let mut original_recipes = BTreeMap::new();
        if boundary_first {
            let mut tail = puzzle.clone();
            tail.targets = completion[split..].to_vec();
            for value in active_values(&tail)?.difference(&outside) {
                if let Some(&(a, b)) = puzzle.arguments.get(value) {
                    original_recipes.insert(*value, (puzzle.definitions[value].op, a, b));
                    if outside.contains(&a) { boundary.insert(a); }
                    if outside.contains(&b) { boundary.insert(b); }
                }
            }
        }
        retained.definitions.retain(|value, _| outside.contains(value));
        retained.arguments.retain(|value, _| outside.contains(value));
        retained.order.retain(|value| outside.contains(value));
        // A unit may be shared by several rebuilt targets. It is only a recipe
        // here; compile() charges it if a candidate actually requires it.
        if !retained.definitions.contains_key(&1) {
            retained.make(b'/', retained.starts[0], retained.starts[0])?;
        }
        let mut state = GraphState { prefix_cost: retained.compile()?.len(), puzzle: retained,
            pending: completion[split..].to_vec(), rank: 0.0, boundary_first,
            boundary: boundary.into_iter().collect(), original_recipes };
        settle_covered(&mut state);
        state.prefix_cost = state.puzzle.compile()?.len();
        Ok(state)
    }

    fn settle_covered(state: &mut GraphState) {
        let mut missing = Vec::new();
        for &target in &state.pending {
            if state.puzzle.definitions.contains_key(&target) {
                if !state.puzzle.targets.contains(&target) { state.puzzle.targets.push(target); }
            } else { missing.push(target); }
        }
        state.pending = missing;
    }

    fn leaves_for(state: &GraphState, config: &Config, variant: usize) -> Vec<Int> {
        let mut leaves = Vec::new();
        let mut used = BTreeSet::new();
        for &value in &state.puzzle.starts {
            if used.insert(value) { leaves.push(value); }
        }
        if state.boundary_first {
            for &value in &state.boundary {
                if leaves.len() >= config.n_recent { break; }
                if state.puzzle.definitions.contains_key(&value) && used.insert(value) { leaves.push(value); }
            }
        }
        let priority_count = leaves.len();
        let mut add = |value: Int| {
            if state.boundary_first && leaves.len() >= config.n_recent { return; }
            if state.puzzle.definitions.contains_key(&value) && used.insert(value) { leaves.push(value); }
        };
        add(1);
        for exponent in [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32, 40, 48] { add(1i64 << exponent); }
        for value in [3, 5, 7, 9, 11, 13, 15, 17, 19, 23, 31] { add(value); }
        drop(add);
        for &value in state.puzzle.targets.iter().rev() {
            if leaves.len() >= config.n_done { break; }
            if state.boundary_first && leaves.len() >= config.n_recent { break; }
            if used.insert(value) { leaves.push(value); }
        }
        let mut intermediate = state.puzzle.order.clone();
        if variant % 3 == 1 {
            let mut uses: BTreeMap<Int, usize> = BTreeMap::new();
            for &(a, b) in state.puzzle.arguments.values() {
                *uses.entry(a).or_default() += 1;
                *uses.entry(b).or_default() += 1;
            }
            intermediate.sort_by_key(|value| (std::cmp::Reverse(uses.get(value).copied().unwrap_or(0)), *value));
        } else if variant % 3 == 0 { intermediate.reverse(); }
        for value in intermediate {
            if leaves.len() >= config.n_recent { break; }
            if used.insert(value) { leaves.push(value); }
        }
        // Alternative insertion order also exposes different dictionary recipes
        // when several expressions produce the same value at the same cost.
        if variant % 2 != 0 {
            let protected = if state.boundary_first { priority_count }
                            else { state.puzzle.starts.len().min(leaves.len()) };
            leaves[protected..].reverse();
        }
        leaves
    }

    fn build_candidate(state: &GraphState, dictionary: &Dictionary, search: &Search,
                       target: Int, upper_bound: usize, budget: &mut Budget) -> Outcome<Option<GraphState>> {
        let mut candidate = state.clone();
        if search.end >= 0 { candidate.puzzle.reconstruct(dictionary, search, target)?; }
        else { binary_value(&mut candidate.puzzle, target)?; }
        finish_candidate(candidate, upper_bound, budget)
    }

    fn finish_candidate(mut candidate: GraphState, upper_bound: usize,
                        budget: &mut Budget) -> Outcome<Option<GraphState>> {
        settle_covered(&mut candidate);
        if candidate.pending.is_empty() { budget.completed += 1; }
        if arithmetic_count(&candidate.puzzle)? >= upper_bound { return Ok(None); }
        candidate.prefix_cost = candidate.puzzle.compile()?.len();
        candidate.rank = candidate.prefix_cost as f64;
        Ok(Some(candidate))
    }

    fn original_value(puzzle: &mut Puzzle, value: Int,
                      recipes: &BTreeMap<Int, (u8, Int, Int)>) -> Outcome<Int> {
        if puzzle.definitions.contains_key(&value) { return Ok(value); }
        let &(op, a, b) = recipes.get(&value).ok_or_else(|| "missing original tail recipe".to_string())?;
        let a = original_value(puzzle, a, recipes)?;
        let b = original_value(puzzle, b, recipes)?;
        puzzle.make(op, a, b)
    }

    fn warm_candidate(state: &GraphState, target: Int, upper_bound: usize,
                      budget: &mut Budget) -> Outcome<Option<GraphState>> {
        let mut candidate = state.clone();
        original_value(&mut candidate.puzzle, target, &state.original_recipes)?;
        finish_candidate(candidate, upper_bound, budget)
    }

    fn binary_value(puzzle: &mut Puzzle, target: Int) -> Outcome<Int> {
        if puzzle.definitions.contains_key(&target) { return Ok(target); }
        if target <= 1 { return Err("missing binary unit".into()); }
        let half = binary_value(puzzle, target / 2)?;
        let twice = puzzle.make(b'+', half, half)?;
        if target % 2 != 0 { puzzle.make(b'+', twice, 1) } else { Ok(twice) }
    }

    fn lookahead(candidate: &GraphState, config: &Config, budget: &mut Budget,
                 variant: usize) -> Outcome<Option<f64>> {
        if candidate.pending.is_empty() { return Ok(Some(candidate.prefix_cost as f64)); }
        if !budget.available() { return Ok(None); }
        let mut dictionary = Dictionary::new(candidate.puzzle.limit, config.bits);
        dictionary.build(&leaves_for(candidate, config, variant));
        let mut remaining = 0usize;
        for &target in &candidate.pending {
            let Some(search) = budget.search(&dictionary, config, target) else { return Ok(None); };
            let mut future = candidate.puzzle.clone();
            let built = if search.end >= 0 { future.reconstruct(&dictionary, &search, target) }
                        else { binary_value(&mut future, target).map(|_| ()) };
            let mut future_cost = None;
            if built.is_ok() {
                future.targets.push(target);
                if let Ok(program) = future.compile() { future_cost = Some(program.len()); }
            }
            if candidate.boundary_first {
                let mut original = candidate.puzzle.clone();
                if original_value(&mut original, target, &candidate.original_recipes).is_ok() {
                    original.targets.push(target);
                    if let Ok(program) = original.compile() {
                        future_cost = Some(future_cost.map_or(program.len(), |cost| cost.min(program.len())));
                    }
                }
            }
            let Some(future_cost) = future_cost else { return Ok(None); };
            // Prefix reload cost can change when a future use is added. The
            // nonnegative delta is a ranking heuristic, never a pruning bound.
            remaining += future_cost.saturating_sub(candidate.prefix_cost);
        }
        Ok(Some(candidate.prefix_cost as f64 + 0.4 * remaining as f64))
    }

    fn expanded_states(state: &GraphState, config: &Config, budget: &mut Budget,
                       variant: usize, upper_bound: usize) -> Outcome<Vec<GraphState>> {
        if !budget.available() { return Ok(Vec::new()); }
        let mut dictionary = Dictionary::new(state.puzzle.limit, config.bits);
        dictionary.build(&leaves_for(state, config, variant));
        let mut candidates = Vec::new();
        for &target in &state.pending {
            let Some(search) = budget.search(&dictionary, config, target) else { break; };
            if let Ok(Some(candidate)) = build_candidate(state, &dictionary, &search, target, upper_bound, budget) {
                candidates.push(candidate);
            }
            if state.boundary_first && budget.available() {
                if let Ok(Some(candidate)) = warm_candidate(state, target, upper_bound, budget) {
                    candidates.push(candidate);
                }
            }
        }
        if state.boundary_first {
            let mut seen = BTreeSet::new();
            let mut unique = Vec::new();
            for candidate in candidates {
                if seen.insert(graph_key(&candidate)?) { unique.push(candidate); }
            }
            candidates = unique;
        }
        candidates.sort_by_key(|candidate| candidate.prefix_cost);
        candidates.truncate(SHORTLIST);
        // Two independently searched derivations of the cheapest next target
        // can have useful intermediates even when their immediate cost ties.
        if let Some(cheapest) = candidates.first() {
            let next_target = cheapest.puzzle.targets.iter().copied()
                .find(|value| !state.puzzle.targets.contains(value));
            if let Some(target) = next_target {
                for (offset, alpha_scale) in [(1, 0.65), (2, 1.45)] {
                    if !budget.available() { break; }
                    let mut alternate_dictionary = Dictionary::new(state.puzzle.limit, config.bits);
                    alternate_dictionary.build(&leaves_for(state, config, variant + offset));
                    let mut alternate_config = config.clone();
                    alternate_config.alpha *= alpha_scale;
                    let Some(search) = budget.search(&alternate_dictionary, &alternate_config, target) else { break; };
                    if let Ok(Some(candidate)) = build_candidate(state, &alternate_dictionary, &search, target, upper_bound, budget) {
                        candidates.push(candidate);
                    }
                }
            }
        }
        let mut unique = BTreeSet::new();
        let mut ranked = Vec::new();
        for mut candidate in candidates {
            if !unique.insert(graph_key(&candidate)?) { continue; }
            let Some(rank) = lookahead(&candidate, config, budget, variant)? else { continue; };
            candidate.rank = rank;
            ranked.push(candidate);
        }
        ranked.sort_by(|a, b| a.rank.total_cmp(&b.rank)
            .then(a.prefix_cost.cmp(&b.prefix_cost)).then(a.pending.cmp(&b.pending)));
        ranked.truncate(GRAPH_WIDTH);
        Ok(ranked)
    }

    // Produce a complete incumbent before the more expensive graph beam. This
    // also makes a short time budget useful when it cannot finish every branch.
    fn greedy_finish(mut state: GraphState, config: &Config, budget: &mut Budget,
                     variant: usize, upper_bound: usize) -> Outcome<Option<Program>> {
        while !state.pending.is_empty() {
            if !budget.available() { return Ok(None); }
            let mut dictionary = Dictionary::new(state.puzzle.limit, config.bits);
            dictionary.build(&leaves_for(&state, config, variant));
            let mut selected: Option<GraphState> = None;
            for &target in &state.pending {
                let Some(search) = budget.search(&dictionary, config, target) else { break; };
                let mut choices = Vec::new();
                if let Ok(Some(candidate)) = build_candidate(&state, &dictionary, &search, target, upper_bound, budget) {
                    choices.push(candidate);
                }
                if state.boundary_first && budget.available() {
                    if let Ok(Some(candidate)) = warm_candidate(&state, target, upper_bound, budget) {
                        choices.push(candidate);
                    }
                }
                for candidate in choices {
                    let key = (candidate.prefix_cost, candidate.pending.len());
                    if selected.as_ref().is_none_or(|previous| key < (previous.prefix_cost, previous.pending.len())) {
                        selected = Some(candidate);
                    }
                }
            }
            let Some(candidate) = selected else { return Ok(None); };
            state = candidate;
        }
        let program = state.puzzle.compile()?;
        Ok((program.len() < upper_bound).then_some(program))
    }

    fn reconstruct_tail(puzzle: &Puzzle, completion: &[Int], length: usize, config: &Config,
                        budget: &mut Budget, variant: usize, upper_bound: usize) -> Outcome<Option<Program>> {
        let Ok(initial) = tail_state(puzzle, completion, length, budget.boundary_first) else { return Ok(None); };
        let mut best = greedy_finish(initial.clone(), config, budget, variant, upper_bound)?;
        let mut beam = vec![initial];
        let mut bound = upper_bound;
        if let Some(program) = &best { bound = program.len(); }
        for _ in 0..=length {
            if !budget.available() { break; }
            // Leave enough time for one complete continuation instead of
            // spending the final part of the budget on partial lookaheads.
            if budget.started.elapsed().as_secs_f64() > budget.duration.as_secs_f64() * 0.65 {
                if let Some(state) = beam.first() {
                    if let Some(program) = greedy_finish(state.clone(), config, budget, variant, bound)? {
                        best = Some(program);
                    }
                }
                break;
            }
            let mut following = Vec::new();
            let mut unique = BTreeSet::new();
            for state in beam {
                if state.pending.is_empty() {
                    if let Ok(program) = state.puzzle.compile() {
                        if program.len() < bound { bound = program.len(); best = Some(program); }
                    }
                    continue;
                }
                for candidate in expanded_states(&state, config, budget, variant, bound)? {
                    if unique.insert(graph_key(&candidate)?) { following.push(candidate); }
                }
            }
            following.sort_by(|a, b| a.rank.total_cmp(&b.rank)
                .then(a.prefix_cost.cmp(&b.prefix_cost)).then(a.pending.cmp(&b.pending)));
            following.truncate(GRAPH_WIDTH);
            // Collect complete states before a deadline discards the next layer.
            for state in &following {
                if state.pending.is_empty() {
                    if let Ok(program) = state.puzzle.compile() {
                        if program.len() < bound { bound = program.len(); best = Some(program); }
                    }
                }
            }
            beam = following;
            if beam.is_empty() || !budget.available() { break; }
        }
        Ok(best)
    }

    fn optimize(puzzle: &mut Puzzle, hint: &Program, config: &Config,
                budget: &mut Budget) -> Outcome<Program> {
        puzzle.load_hint(hint)?;
        let mut best = hint.clone();
        if let Ok(program) = puzzle.compile() {
            if program.len() < best.len() { best = program; }
        }
        for pass in 0..config.passes {
            let mut improved = false;
            for attempt in 0..config.trials {
                if !budget.available() { break; }
                puzzle.load_hint(&best)?;
                let completion = completion_order(puzzle);
                if completion.is_empty() { return Ok(best); }
                let length = completion.len().min(6 + (attempt + config.seed as usize) % 5);
                budget.neighborhoods += 1;
                let variant = config.seed as usize + attempt + pass * config.trials;
                if let Some(candidate) = reconstruct_tail(puzzle, &completion, length, config, budget, variant, best.len())? {
                    // Replay using the same strict hint loader before exposing
                    // any candidate to the independently validating wrapper.
                    let mut checked = puzzle.clone();
                    if candidate.len() < best.len() && checked.load_hint(&candidate).is_ok() {
                        best = candidate;
                        improved = true;
                        budget.accepted += 1;
                    }
                }
            }
            if !improved || !budget.available() { break; }
        }
        Ok(best)
    }

    pub fn optimizer_run() -> Outcome<()> {
        let arguments: Vec<String> = env::args().collect();
        fn parameter<T: std::str::FromStr>(arguments: &[String], index: usize, default: T) -> Outcome<T> {
            if let Some(value) = arguments.get(index) { value.parse().map_err(|_| "invalid argument".into()) }
            else { Ok(default) }
        }
        let config = Config { width: parameter(&arguments, 1, 3)?, alpha: parameter(&arguments, 2, 0.18)?,
            n_done: parameter(&arguments, 3, 52)?, n_recent: parameter(&arguments, 4, 56)?,
            bits: parameter(&arguments, 5, 20)?, seed: parameter(&arguments, 7, 0)?,
            trials: parameter(&arguments, 8, 12)?, passes: parameter(&arguments, 9, 1)? };
        let mode: i32 = parameter(&arguments, 6, 6)?;
        if mode != 6 { return Err("Rust optimizer uses the mode-6 hint protocol".into()); }
        if config.width == 0 || config.bits == 0 || config.bits > 26 || config.trials > 4096
            || config.passes == 0 || config.passes > 100 { return Err("invalid search budget".into()); }
        let stdin = io::stdin();
        let mut scanner = Scanner::new(BufReader::new(stdin.lock()));
        let stdout = io::stdout();
        let mut writer = BufWriter::new(stdout.lock());
        while let Some(id) = scanner.next()? {
            let limit: Int = scanner.integer()?;
            let starts_count: usize = scanner.integer()?;
            let targets_count: usize = scanner.integer()?;
            if !(1..=1_000_000_000_000_000_000).contains(&limit) || starts_count == 0
                || starts_count > 48 || targets_count > 4096 { return Err("invalid puzzle dimensions".into()); }
            let mut starts = Vec::with_capacity(starts_count);
            let mut targets = Vec::with_capacity(targets_count);
            for _ in 0..starts_count { starts.push(scanner.integer()?); }
            for _ in 0..targets_count { targets.push(scanner.integer()?); }
            let count: usize = scanner.integer()?;
            if count > 4096 { return Err("invalid hint instruction count".into()); }
            let mut hint = Vec::with_capacity(count);
            for _ in 0..count {
                let token = scanner.next()?.ok_or_else(|| "truncated hint program".to_string())?;
                if token.len() != 1 { return Err("invalid hint operator".into()); }
                hint.push([token.as_bytes()[0] as i32, scanner.integer()?, scanner.integer()?, scanner.integer()?]);
            }
            let mut puzzle = Puzzle { limit, starts, targets, definitions: BTreeMap::new(),
                arguments: BTreeMap::new(), order: Vec::new() };
            let mut budget = Budget::new(&config)?;
            let program = optimize(&mut puzzle, &hint, &config, &mut budget)?;
            write!(writer, "{} [", id).map_err(|error| error.to_string())?;
            for (index, instruction) in program.iter().enumerate() {
                if index != 0 { write!(writer, ",").map_err(|error| error.to_string())?; }
                write!(writer, "[\"{}\",{},{},{}]", instruction[0] as u8 as char,
                    instruction[1], instruction[2], instruction[3]).map_err(|error| error.to_string())?;
            }
            writeln!(writer, "]").map_err(|error| error.to_string())?;
            writer.flush().map_err(|error| error.to_string())?;
            if env::var_os("SANTA2026_OPTIMIZER_TRACE").is_some() {
                eprintln!("optimizer id={} before={} after={} neighborhoods={} searches={} completed={} accepted={} elapsed_ms={}",
                    id, hint.len(), program.len(), budget.neighborhoods, budget.searches, budget.completed,
                    budget.accepted, budget.started.elapsed().as_millis());
            }
        }
        Ok(())
    }

    #[cfg(test)]
    mod optimizer_tests {
        use super::*;

        fn config() -> Config {
            Config { width: 3, alpha: 0.18, n_done: 32, n_recent: 40, bits: 14,
                seed: 0, trials: 3, passes: 1 }
        }
        fn puzzle(starts: Vec<Int>, targets: Vec<Int>, limit: Int) -> Puzzle {
            Puzzle { limit, starts, targets, definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() }
        }
        #[test]
        fn jointly_reconstructs_shared_tail() {
            let mut puzzle = puzzle(vec![10], vec![100, 200, 300], 500);
            let hint = vec![[43,0,0,1], [43,1,0,2], [43,2,0,3], [43,3,0,4],
                [43,4,4,5], [43,5,5,6], [43,5,6,7]];
            let config = config();
            let mut budget = Budget::new(&config).unwrap();
            let result = optimize(&mut puzzle, &hint, &config, &mut budget).unwrap();
            assert_eq!(result.len(), 3);
            puzzle.load_hint(&result).unwrap();
        }
        #[test]
        fn full_seed_bank_retains_overwrite_hint() {
            let mut puzzle = puzzle((1..=48).collect(), vec![49], 100);
            let hint = vec![[43,0,47,0]];
            let config = config();
            let mut budget = Budget::new(&config).unwrap();
            assert_eq!(optimize(&mut puzzle, &hint, &config, &mut budget).unwrap(), hint);
        }
        #[test]
        fn zero_trials_preserves_normalization_and_coverage() {
            let mut puzzle = puzzle(vec![3], vec![3, 1], 9);
            let hint = vec![[47,0,0,1], [43,0,1,2]];
            let mut config = config(); config.trials = 0;
            let mut budget = Budget::new(&config).unwrap();
            let result = optimize(&mut puzzle, &hint, &config, &mut budget).unwrap();
            assert_eq!(result, vec![[47,0,0,1]]);
            assert_eq!(budget.searches, 0);
        }
        #[test]
        fn lookahead_prices_every_remaining_target_construction() {
            let mut puzzle = puzzle(vec![10], vec![100], 500);
            puzzle.load_hint(&vec![[42,0,0,1]]).unwrap();
            let state = GraphState { puzzle, pending: vec![200,300], prefix_cost: 1, rank: 0.0,
                boundary_first: false, boundary: Vec::new(), original_recipes: BTreeMap::new() };
            let config = config();
            let mut budget = Budget::new(&config).unwrap();
            let rank = lookahead(&state, &config, &mut budget, 0).unwrap().unwrap();
            // 200 costs one additional operation; 300 costs two. Neither has
            // been covered yet, so a covered-target-count score cannot do this.
            assert!((rank - 2.2).abs() < 1e-12);
            assert_eq!(budget.searches, 2);
        }
        #[test]
        fn expired_budget_returns_a_valid_incumbent_without_search() {
            let mut puzzle = puzzle(vec![3], vec![1], 9);
            let hint = vec![[47,0,0,1]];
            let config = config();
            let mut budget = Budget::new(&config).unwrap();
            budget.duration = Duration::ZERO;
            assert_eq!(optimize(&mut puzzle, &hint, &config, &mut budget).unwrap(), hint);
            assert_eq!(budget.searches, 0);
        }
        #[test]
        fn retained_prefix_keeps_the_shared_ancestor_cone() {
            let mut puzzle = puzzle(vec![3], vec![5,10,15,25], 25);
            let hint = vec![[47,0,0,1],[45,0,1,2],[43,0,2,3],
                [43,3,3,4],[43,4,3,5],[43,5,4,6]];
            puzzle.load_hint(&hint).unwrap();
            let state = tail_state(&puzzle, &completion_order(&puzzle), 2, false).unwrap();
            assert_eq!(state.puzzle.targets, vec![5,10]);
            assert_eq!(state.pending, vec![15,25]);
            assert!(state.puzzle.arguments.contains_key(&5));
            assert!(state.puzzle.arguments.contains_key(&10));
            assert!(!state.puzzle.definitions.contains_key(&15));
            assert!(!state.puzzle.definitions.contains_key(&25));
        }
        #[test]
        fn exact_boundary_precedes_palette_and_survives_leaf_perturbation() {
            let mut puzzle = puzzle(vec![3], vec![5,10,15,25], 25);
            let hint = vec![[47,0,0,1],[45,0,1,2],[43,0,2,3],
                [43,3,3,4],[43,4,3,5],[43,5,4,6]];
            puzzle.load_hint(&hint).unwrap();
            let completion = completion_order(&puzzle);
            let boundary = tail_state(&puzzle, &completion, 2, true).unwrap();
            let original = tail_state(&puzzle, &completion, 2, false).unwrap();
            assert_eq!(boundary.boundary, vec![5,10]);
            let mut config = config(); config.n_recent = 3;
            assert_eq!(leaves_for(&boundary, &config, 0), vec![3,5,10]);
            assert_eq!(leaves_for(&boundary, &config, 1), vec![3,5,10]);
            // Default selection retains its original palette-first behavior.
            assert_eq!(&leaves_for(&original, &config, 0)[..3], &[3,1,2]);
        }
        #[test]
        fn boundary_budget_keeps_all_starts_even_when_the_cap_is_smaller() {
            let mut puzzle = puzzle(vec![3,5], vec![8], 25);
            puzzle.load_hint(&vec![[43,0,1,2]]).unwrap();
            let state = tail_state(&puzzle, &completion_order(&puzzle), 1, true).unwrap();
            let mut config = config(); config.n_recent = 1;
            assert_eq!(leaves_for(&state, &config, 1), vec![3,5]);
        }
        #[test]
        fn original_recipe_is_a_paid_warm_start_with_an_incomplete_leaf_pool() {
            let mut puzzle = puzzle(vec![3], vec![5,10,15,25], 25);
            let hint = vec![[47,0,0,1],[45,0,1,2],[43,0,2,3],
                [43,3,3,4],[43,4,3,5],[43,5,4,6]];
            puzzle.load_hint(&hint).unwrap();
            let state = tail_state(&puzzle, &completion_order(&puzzle), 2, true).unwrap();
            let mut config = config(); config.n_recent = 1;
            assert_eq!(leaves_for(&state, &config, 0), vec![3]);
            let mut budget = Budget::new(&config).unwrap();
            let mut candidate = warm_candidate(&state, 15, hint.len(), &mut budget).unwrap().unwrap();
            assert_eq!(candidate.prefix_cost, 5);
            assert_eq!(candidate.pending, vec![25]);
            let program = candidate.puzzle.compile().unwrap();
            candidate.puzzle.load_hint(&program).unwrap();
        }
        #[test]
        fn boundary_mode_completes_a_shorter_shared_tail_and_preserves_fallback() {
            let mut puzzle = puzzle(vec![10], vec![100,200,300], 500);
            let hint = vec![[43,0,0,1], [43,1,0,2], [43,2,0,3], [43,3,0,4],
                [43,4,4,5], [43,5,5,6], [43,5,6,7]];
            let config = config();
            let mut budget = Budget::new(&config).unwrap(); budget.boundary_first = true;
            let result = optimize(&mut puzzle, &hint, &config, &mut budget).unwrap();
            assert_eq!(result.len(), 3);
            puzzle.load_hint(&result).unwrap();
            let mut full = super::optimizer_tests::puzzle((1..=48).collect(), vec![49], 100);
            let overwrite = vec![[43,0,47,0]];
            let mut budget = Budget::new(&config).unwrap(); budget.boundary_first = true;
            assert_eq!(optimize(&mut full, &overwrite, &config, &mut budget).unwrap(), overwrite);
        }
    }
}

fn main() {
    if let Err(error) = engine::optimizer_run() {
        eprintln!("Solver error: {}", error);
        std::process::exit(1);
    }
}
