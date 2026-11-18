// SPDX-License-Identifier: Apache-2.0
// Shared-GCD group reconstruction developed independently for this repository.
// Reuses only the licensed dictionary/search/allocator port in repair.rs:
// Tony Li and konbu17; see repair.rs and THIRD_PARTY.md for attribution.
// Same mode-6 hint protocol and positional arguments as repair.rs.
// SANTA2026_GROUPS_MS (or SANTA2026_OPTIMIZER_MS) sets the per-puzzle
// cooperative budget in milliseconds; default 2000, allowed 1..600000.

#[allow(dead_code)]
mod engine {
    include!("repair.rs");

    use std::time::{Duration, Instant};

    struct Budget {
        started: Instant,
        duration: Duration,
        groups: usize,
        searches: usize,
        accepted: usize,
    }

    impl Budget {
        fn new() -> Outcome<Self> {
            let setting = env::var("SANTA2026_GROUPS_MS")
                .or_else(|_| env::var("SANTA2026_OPTIMIZER_MS"));
            let milliseconds: u64 = match setting {
                Ok(value) => value.parse().map_err(|_| "invalid factor-group time budget")?,
                Err(_) => 2000,
            };
            if milliseconds == 0 || milliseconds > 600_000 {
                return Err("factor-group budget must be within 1..600000 milliseconds".into());
            }
            Ok(Self { started: Instant::now(), duration: Duration::from_millis(milliseconds),
                groups: 0, searches: 0, accepted: 0 })
        }
        fn available(&self) -> bool { self.started.elapsed() < self.duration }
    }

    fn gcd(mut a: Int, mut b: Int) -> Int {
        while b != 0 { (a, b) = (b, a % b); }
        a
    }

    // Check live dependencies before invoking the recursive physical allocator.
    // Numeric value identity is shared, so a restored old recipe can otherwise
    // make a new construction depend on itself through a different value.
    fn live_values(puzzle: &Puzzle) -> Outcome<BTreeSet<Int>> {
        let seeds: BTreeSet<Int> = puzzle.starts.iter().copied().collect();
        let mut colors: BTreeMap<Int, u8> = seeds.iter().map(|&value| (value, 2)).collect();
        for &target in &puzzle.targets {
            let mut stack = vec![(target, false)];
            while let Some((value, leaving)) = stack.pop() {
                if seeds.contains(&value) { continue; }
                if leaving { colors.insert(value, 2); continue; }
                match colors.get(&value).copied().unwrap_or(0) {
                    2 => continue,
                    1 => return Err("cyclic factor-group value graph".into()),
                    _ => (),
                }
                let expression = puzzle.definitions.get(&value).ok_or("missing factor-group value")?;
                let &(a, b) = puzzle.arguments.get(&value).ok_or("missing factor-group recipe")?;
                if a <= 0 || b <= 0 || a > puzzle.limit || b > puzzle.limit
                    || (expression.op == b'/' && a % b != 0)
                    || apply(expression.op, a, b)? != value {
                    return Err("illegal factor-group recipe".into());
                }
                colors.insert(value, 1);
                stack.push((value, true));
                stack.push((b, false));
                stack.push((a, false));
            }
        }
        Ok(colors.into_keys().collect())
    }

    fn ancestors(puzzle: &Puzzle, roots: impl IntoIterator<Item = Int>) -> BTreeSet<Int> {
        let mut values = BTreeSet::new();
        let mut pending: Vec<Int> = roots.into_iter().collect();
        while let Some(value) = pending.pop() {
            if values.insert(value) {
                if let Some(&(a, b)) = puzzle.arguments.get(&value) {
                    pending.push(a); pending.push(b);
                }
            }
        }
        values
    }

    #[derive(Clone)]
    struct Group { factor: Int, targets: Vec<Int>, exclusive: usize, novel: bool }

    fn ranked_groups(puzzle: &Puzzle, seed: u64, budget: &Budget) -> Vec<Group> {
        let targets: Vec<Int> = puzzle.targets.iter().copied()
            .filter(|value| !puzzle.starts.contains(value)).collect::<BTreeSet<_>>()
            .into_iter().collect();
        let mut factors = BTreeSet::new();
        let mut examined = 0usize;
        'pairs: for (i, &a) in targets.iter().enumerate() {
            for &b in &targets[..i] {
                examined += 1;
                if examined & 127 == 0 && !budget.available() { break 'pairs; }
                let factor = gcd(a, b);
                if factor > 1 { factors.insert(factor); }
            }
        }
        let mut closure = BTreeMap::new();
        for &target in &targets {
            if !budget.available() { return Vec::new(); }
            closure.insert(target, ancestors(puzzle, [target]));
        }
        let mut groups = Vec::new();
        for factor in factors {
            if !budget.available() { break; }
            let members: Vec<Int> = targets.iter().copied().filter(|target| target % factor == 0).collect();
            if members.len() < 2 { continue; }
            let selected: BTreeSet<Int> = members.iter().copied().collect();
            let inside: BTreeSet<Int> = members.iter().flat_map(|target| closure[target].iter().copied()).collect();
            let outside: BTreeSet<Int> = targets.iter().filter(|target| !selected.contains(target))
                .flat_map(|target| closure[target].iter().copied()).chain(puzzle.starts.iter().copied()).collect();
            let exclusive = inside.difference(&outside).filter(|value| puzzle.arguments.contains_key(value)).count();
            groups.push(Group { factor, targets: members, exclusive,
                novel: !puzzle.definitions.contains_key(&factor) });
        }
        groups.sort_by_key(|group| {
            // This is a ranking estimate only. Actual helper, quotient, spill,
            // and multiplication instructions are charged by full compilation.
            let payoff = group.exclusive as isize - group.targets.len() as isize - isize::from(group.novel);
            (std::cmp::Reverse(payoff), std::cmp::Reverse(group.novel),
             std::cmp::Reverse(group.targets.len()), group.factor)
        });
        if !groups.is_empty() {
            let shift = (seed % groups.len() as u64) as usize;
            groups.rotate_left(shift);
        }
        groups
    }

    fn retained_context(puzzle: &Puzzle, group: &Group) -> Outcome<Puzzle> {
        let selected: BTreeSet<Int> = group.targets.iter().copied().collect();
        let outside = ancestors(puzzle, puzzle.targets.iter().copied().filter(|target| !selected.contains(target)));
        let inside = ancestors(puzzle, group.targets.iter().copied());
        let mut removed: BTreeSet<Int> = inside.difference(&outside)
            .filter(|value| !puzzle.starts.contains(value)).copied().collect();
        for &target in &group.targets { removed.extend(puzzle.descendants(target)); }
        let mut retained = puzzle.clone();
        retained.definitions.retain(|value, _| !removed.contains(value));
        retained.arguments.retain(|value, _| !removed.contains(value));
        retained.order.retain(|value| !removed.contains(value));
        // The input graph has been pruned to live values. No live survivor may
        // rely on an exclusive ancestor or blocked descendant removed above.
        if retained.arguments.values().any(|(a, b)| !retained.definitions.contains_key(a)
            || !retained.definitions.contains_key(b)) {
            return Err("factor-group context is not dependency closed".into());
        }
        Ok(retained)
    }

    fn leaves(puzzle: &Puzzle, priority: &[Int], config: &Config, reverse: bool) -> Vec<Int> {
        let mut result = Vec::new();
        let mut used = BTreeSet::new();
        for &value in &puzzle.starts {
            if used.insert(value) { result.push(value); }
        }
        let mut add = |value: Int| {
            if result.len() < config.n_recent && puzzle.definitions.contains_key(&value) && used.insert(value) {
                result.push(value);
            }
        };
        for &value in priority { add(value); }
        add(1);
        for exponent in [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32, 40, 48] { add(1i64 << exponent); }
        for &value in puzzle.targets.iter().rev() { add(value); }
        if reverse { for &value in &puzzle.order { add(value); } }
        else { for &value in puzzle.order.iter().rev() { add(value); } }
        result
    }

    // Reuse Dictionary's arithmetic implementation, with cancellation in the
    // two nested construction loops rather than only after a full build.
    fn bounded_dictionary(dictionary: &mut Dictionary, values: &[Int], budget: &Budget) -> bool {
        if !budget.available() { return false; }
        dictionary.index.clear(); dictionary.expressions.clear();
        for group in &mut dictionary.sorted { group.clear(); }
        for &value in values { dictionary.add(value, -1, -1, 0, 0); }
        dictionary.n0 = dictionary.expressions.len();
        let mut examined = 0usize;
        for i in 0..dictionary.n0 {
            for j in 0..=i {
                examined += 1;
                if examined & 127 == 0 && !budget.available() { return false; }
                dictionary.combine(i, j, 1);
            }
        }
        dictionary.n1 = dictionary.expressions.len();
        for i in dictionary.n0..dictionary.n1 {
            for j in 0..dictionary.n0 {
                examined += 1;
                if examined & 127 == 0 && !budget.available() { return false; }
                dictionary.combine(i, j, 2);
            }
        }
        for (i, expression) in dictionary.expressions.iter().enumerate() {
            if i & 4095 == 0 && !budget.available() { return false; }
            dictionary.sorted[expression.cost as usize].push((expression.value, i as i32));
        }
        for group in &mut dictionary.sorted {
            if !budget.available() { return false; }
            group.sort_unstable();
        }
        budget.available()
    }

    fn bounded_search<'a>(dictionary: &'a Dictionary, config: &Config, target: Int,
                          budget: &mut Budget) -> Option<Search<'a>> {
        if !budget.available() { return None; }
        budget.searches += 1;
        let mut search = Search::new(dictionary, config);
        search.history.push(State { value: target, cost: 0, parent: -1, link: Link::default(), rank: 0.0 });
        let known = dictionary.index.get(target);
        if known >= 0 {
            search.end = 0; search.terminal = known;
            search.best = dictionary.expressions[known as usize].cost;
            return Some(search);
        }
        let mut front = vec![0usize];
        for _ in 0..20 {
            if front.is_empty() { break; }
            search.next.clear();
            for &parent in &front {
                if !budget.available() { return None; }
                if search.history[parent].cost + 3 < search.best { search.expand(parent); }
            }
            front.clear();
            for state in &search.next {
                if state.cost + 3 >= search.best { continue; }
                search.visited.insert(state.value, state.cost);
                front.push(search.history.len()); search.history.push(*state);
            }
        }
        Some(search)
    }

    fn binary(puzzle: &mut Puzzle, value: Int) -> Outcome<Int> {
        if puzzle.definitions.contains_key(&value) { return Ok(value); }
        if value == 1 { return puzzle.make(b'/', puzzle.starts[0], puzzle.starts[0]); }
        let half = binary(puzzle, value / 2)?;
        let twice = puzzle.make(b'+', half, half)?;
        if value & 1 != 0 { binary(puzzle, 1)?; puzzle.make(b'+', twice, 1) } else { Ok(twice) }
    }

    fn construct(puzzle: &mut Puzzle, value: Int, priority: &[Int], config: &Config,
                 reverse: bool, budget: &mut Budget) -> Outcome<bool> {
        if !budget.available() { return Ok(false); }
        if puzzle.definitions.contains_key(&value) { return Ok(true); }
        if !puzzle.definitions.contains_key(&1) { puzzle.make(b'/', puzzle.starts[0], puzzle.starts[0])?; }
        let mut dictionary = Dictionary::new(puzzle.limit, config.bits);
        if !bounded_dictionary(&mut dictionary, &leaves(puzzle, priority, config, reverse), budget) {
            return Ok(false);
        }
        let Some(search) = bounded_search(&dictionary, config, value, budget) else { return Ok(false); };
        if search.end >= 0 { puzzle.reconstruct(&dictionary, &search, value)?; }
        else { binary(puzzle, value)?; }
        Ok(true)
    }

    fn attempt_group(original: &Puzzle, group: &Group, config: &Config, reverse: bool,
                     upper_bound: usize, budget: &mut Budget) -> Outcome<Option<Program>> {
        let mut trial = retained_context(original, group)?;
        // Exact retained cut values enter the dictionary before the palette.
        // They can be useful even when they are neither recent nor targets.
        let boundary: BTreeSet<Int> = original.arguments.iter()
            .filter(|(value, _)| !trial.definitions.contains_key(value))
            .flat_map(|(_, &(a, b))| [a, b]).filter(|value| trial.definitions.contains_key(value)).collect();
        let mut priority: Vec<Int> = boundary.into_iter().collect();
        if !construct(&mut trial, group.factor, &priority, config, reverse, budget)? { return Ok(None); }
        priority.insert(0, group.factor);
        let mut targets = group.targets.clone();
        if reverse { targets.reverse(); }
        for target in targets {
            let quotient = target / group.factor;
            if !construct(&mut trial, quotient, &priority, config, reverse, budget)? { return Ok(None); }
            if target != group.factor {
                // A newly searched factor/quotient may incidentally reproduce
                // an old target. Do not replace its recipe with a cyclic one.
                if ancestors(&trial, [group.factor, quotient]).contains(&target) { return Ok(None); }
                trial.definitions.insert(target, Expr { value: target, a: -1, b: -1, op: b'*', cost: 0 });
                trial.arguments.insert(target, (group.factor, quotient));
                if !trial.order.contains(&target) { trial.order.push(target); }
            }
            priority.insert(1, quotient); priority.insert(1, target);
        }
        // Restoring only absent definitions retains new shared recipes while
        // repairing all downstream outputs removed with the blocked roots.
        for (&value, &expression) in &original.definitions {
            if !trial.definitions.contains_key(&value) {
                trial.definitions.insert(value, expression);
                if let Some(&args) = original.arguments.get(&value) { trial.arguments.insert(value, args); }
            }
        }
        live_values(&trial)?;
        if !budget.available() { return Ok(None); }
        let candidate = trial.compile()?;
        if candidate.len() >= upper_bound || candidate.len() > 4096 { return Ok(None); }
        let mut checked = original.clone();
        checked.load_hint(&candidate)?;
        Ok(Some(candidate))
    }

    fn optimize(puzzle: &mut Puzzle, hint: &Program, config: &Config, budget: &mut Budget) -> Outcome<Program> {
        puzzle.load_hint(hint)?;
        let mut best = hint.clone();
        for pass in 0..config.passes {
            if !budget.available() { break; }
            puzzle.load_hint(&best)?;
            let live = live_values(puzzle)?;
            puzzle.definitions.retain(|value, _| live.contains(value));
            puzzle.arguments.retain(|value, _| live.contains(value));
            puzzle.order.retain(|value| live.contains(value));
            let groups = ranked_groups(puzzle, config.seed.wrapping_add(pass as u64), budget);
            let mut improved = false;
            for group in groups.iter().take(config.trials) {
                if !budget.available() { break; }
                budget.groups += 1;
                for reverse in [false, true] {
                    if !budget.available() { break; }
                    if let Ok(Some(candidate)) = attempt_group(puzzle, group, config, reverse, best.len(), budget) {
                        best = candidate; improved = true; budget.accepted += 1;
                    }
                }
            }
            if !improved { break; }
        }
        Ok(best)
    }

    pub fn groups_run() -> Outcome<()> {
        fn parameter<T: std::str::FromStr>(args: &[String], i: usize, default: T) -> Outcome<T> {
            args.get(i).map_or(Ok(default), |value| value.parse().map_err(|_| "invalid factor-group argument".into()))
        }
        let args: Vec<String> = env::args().collect();
        let config = Config { width: parameter(&args, 1, 3)?, alpha: parameter(&args, 2, 0.18)?,
            n_done: parameter(&args, 3, 52)?, n_recent: parameter(&args, 4, 56)?, bits: parameter(&args, 5, 20)?,
            seed: parameter(&args, 7, 0)?, trials: parameter(&args, 8, 12)?, passes: parameter(&args, 9, 1)? };
        if parameter(&args, 6, 6i32)? != 6 || config.width == 0 || config.width > 512
            || config.bits == 0 || config.bits > 26 || !config.alpha.is_finite() || config.alpha < 0.0
            || config.trials > 4096 || config.passes == 0 || config.passes > 100 {
            return Err("invalid factor-group search budget or mode".into());
        }
        let stdin = io::stdin(); let stdout = io::stdout();
        let mut scanner = Scanner::new(BufReader::new(stdin.lock()));
        let mut writer = BufWriter::new(stdout.lock());
        while let Some(id) = scanner.next()? {
            let limit: Int = scanner.integer()?;
            let ns: usize = scanner.integer()?; let nt: usize = scanner.integer()?;
            if !(1..=1_000_000_000_000_000_000).contains(&limit) || ns == 0 || ns > 48 || nt > 4096 {
                return Err("invalid factor-group puzzle dimensions".into());
            }
            let mut starts = Vec::new(); let mut targets = Vec::new();
            for _ in 0..ns { starts.push(scanner.integer()?); }
            for _ in 0..nt { targets.push(scanner.integer()?); }
            let count: usize = scanner.integer()?;
            if count > 4096 { return Err("invalid factor-group hint size".into()); }
            let mut hint = Vec::new();
            for _ in 0..count {
                let op = scanner.next()?.ok_or("truncated factor-group hint")?;
                if op.len() != 1 { return Err("invalid factor-group operator".into()); }
                hint.push([op.as_bytes()[0] as i32, scanner.integer()?, scanner.integer()?, scanner.integer()?]);
            }
            let mut puzzle = Puzzle { limit, starts, targets, definitions: BTreeMap::new(),
                arguments: BTreeMap::new(), order: Vec::new() };
            let mut budget = Budget::new()?;
            let best = optimize(&mut puzzle, &hint, &config, &mut budget)?;
            write!(writer, "{} [", id).map_err(|error| error.to_string())?;
            for (i, command) in best.iter().enumerate() {
                if i != 0 { write!(writer, ",").map_err(|error| error.to_string())?; }
                write!(writer, "[\"{}\",{},{},{}]", command[0] as u8 as char, command[1], command[2], command[3])
                    .map_err(|error| error.to_string())?;
            }
            writeln!(writer, "]").map_err(|error| error.to_string())?;
            writer.flush().map_err(|error| error.to_string())?;
            if env::var_os("SANTA2026_GROUPS_TRACE").is_some() {
                eprintln!("groups id={} before={} after={} groups={} searches={} accepted={} elapsed_ms={}",
                    id, hint.len(), best.len(), budget.groups, budget.searches, budget.accepted,
                    budget.started.elapsed().as_millis());
            }
        }
        Ok(())
    }

    #[cfg(test)]
    mod factor_tests {
        use super::*;

        fn puzzle(starts: Vec<Int>, targets: Vec<Int>, limit: Int) -> Puzzle {
            Puzzle { limit, starts, targets, definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() }
        }
        fn config() -> Config {
            Config { width: 3, alpha: 0.18, n_done: 32, n_recent: 40, bits: 14, seed: 0, trials: 4, passes: 1 }
        }
        fn budget() -> Budget {
            Budget { started: Instant::now(), duration: Duration::from_secs(5), groups: 0, searches: 0, accepted: 0 }
        }
        #[test]
        fn synthesizes_an_unseen_shared_factor_for_three_outputs() {
            let mut game = puzzle(vec![2,3,5], vec![14,21,35], 100);
            let hint = vec![[43,0,0,3],[43,2,2,4],[43,4,3,5],[43,1,1,6],
                [42,1,2,7],[43,7,6,8],[42,2,2,9],[43,9,4,10]];
            game.load_hint(&hint).unwrap();
            assert!(!game.definitions.contains_key(&7));
            let result = optimize(&mut game, &hint, &config(), &mut budget()).unwrap();
            assert_eq!(result.len(), 4);
            game.load_hint(&result).unwrap();
            assert!(game.definitions.contains_key(&7));
        }
        #[test]
        fn quotients_and_factor_are_paid_constructions() {
            let mut game = puzzle(vec![2,3,5], vec![77,91], 100);
            let hint = vec![[43,2,2,3],[43,3,3,4],[43,4,4,5],[43,5,5,6],
                [45,6,1,7],[43,6,3,8],[47,0,0,9],[43,8,9,10]];
            game.load_hint(&hint).unwrap();
            for value in [7,11,13] { assert!(!game.definitions.contains_key(&value)); }
            let result = optimize(&mut game, &hint, &config(), &mut budget()).unwrap();
            assert!(result.len() < hint.len());
            assert!(result.len() >= 5);
            game.load_hint(&result).unwrap();
            for value in [7,11,13] { assert!(game.definitions.contains_key(&value)); }
        }
        #[test]
        fn full_seed_bank_preserves_the_overwrite_fallback() {
            let mut game = puzzle((1..=48).collect(), vec![49,98], 100);
            let hint = vec![[43,0,47,0],[43,0,0,1]];
            assert_eq!(optimize(&mut game, &hint, &config(), &mut budget()).unwrap(), hint);
        }
        #[test]
        fn maximum_products_are_legal_and_never_regress() {
            let mut game = puzzle(vec![1_000_000_000,999_999_999],
                vec![1_000_000_000_000_000_000,999_999_999_000_000_000], 1_000_000_000_000_000_000);
            let hint = vec![[42,0,0,2],[42,0,1,3]];
            let result = optimize(&mut game, &hint, &config(), &mut budget()).unwrap();
            assert!(result.len() <= hint.len()); game.load_hint(&result).unwrap();
        }
        #[test]
        fn cycle_guard_rejects_an_overwritten_target_dependency() {
            let mut game = puzzle(vec![3], vec![6], 10);
            game.load_hint(&vec![[43,0,0,1]]).unwrap();
            game.arguments.insert(6, (6,3));
            assert!(live_values(&game).is_err());
        }
        #[test]
        fn expired_budget_returns_the_valid_hint() {
            let mut game = puzzle(vec![2,3,5], vec![14,21], 100);
            let hint = vec![[43,0,2,3],[42,3,0,4],[42,3,1,5]];
            let mut limits = budget(); limits.duration = Duration::ZERO;
            assert_eq!(optimize(&mut game, &hint, &config(), &mut limits).unwrap(), hint);
            assert_eq!(limits.searches, 0);
        }
        #[test]
        fn seed_rotates_the_ranked_gcd_neighborhood() {
            let mut game = puzzle(vec![2,3,5], vec![14,21,35,10], 100);
            let hint = vec![[43,0,2,3],[42,3,0,4],[42,3,1,5],[42,3,2,6],[43,2,2,7]];
            game.load_hint(&hint).unwrap();
            let first = ranked_groups(&game, 0, &budget());
            let next = ranked_groups(&game, 1, &budget());
            assert!(first.len() >= 2);
            assert_eq!(first[1].factor, next[0].factor);
        }
    }
}

fn main() {
    if let Err(error) = engine::groups_run() {
        eprintln!("Factor-group search error: {}", error);
        std::process::exit(1);
    }
}
