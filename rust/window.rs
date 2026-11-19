// SPDX-License-Identifier: Apache-2.0
// Bounded multi-output register-window superoptimization, developed locally.
// Integer replay reuses the Apache-2.0 Tony Li / konbu17 Rust repair importer.
// See repair.rs, THIRD_PARTY.md and licenses/Apache-2.0.txt.
// Mode-6 hint protocol; CLI: seed windows beam candidates milliseconds rename.
// Optional rename=1 / SANTA2026_WINDOW_RENAME=1 permits live-out values in
// different registers and consistently permutes the complete remaining suffix.

#[allow(dead_code)]
mod engine {
    include!("repair.rs");

    use std::collections::HashSet;
    use std::time::{Duration, Instant};

    #[derive(Clone)]
    struct WindowState { bank: [Int; 48], covered: u16, program: Program, rank: usize, hash: u64 }

    struct WindowGoal { registers: Vec<(usize, Int)>, targets: Vec<Int>, all: u16, rename: bool }

    #[derive(Clone, Copy)]
    struct Operation { op: u8, a: usize, b: usize, value: Int, hits: usize, distance: u32 }

    struct WindowConfig { seed: u64, windows: usize, beam: usize, candidates: usize, milliseconds: u64, rename: bool }

    fn bank_hash(bank: &[Int; 48], covered: u16) -> u64 {
        bank.iter().fold(0xcbf29ce484222325u64 ^ u64::from(covered), |hash, &value| {
            (hash ^ value as u64).wrapping_mul(0x100000001b3)
        })
    }

    fn finish(goal: &WindowGoal, bank: &[Int; 48], covered: u16) -> bool {
        covered == goal.all && missing_registers(goal, bank) == 0
    }

    fn live_out_deficits(goal: &WindowGoal, bank: &[Int; 48]) -> BTreeMap<Int, usize> {
        let mut counts: BTreeMap<Int, usize> = BTreeMap::new();
        for &(_, value) in &goal.registers { *counts.entry(value).or_default() += 1; }
        for &value in bank {
            if let Some(count) = counts.get_mut(&value) {
                *count = count.saturating_sub(1);
            }
        }
        counts.retain(|_, count| *count != 0);
        counts
    }

    fn missing_registers(goal: &WindowGoal, bank: &[Int; 48]) -> usize {
        if goal.rename { live_out_deficits(goal, bank).values().sum() }
        else { goal.registers.iter().filter(|&&(register, value)| bank[register] != value).count() }
    }

    // Map old suffix registers to new registers injectively. Equal live-out
    // values still need separate physical copies; this never invents a move.
    fn suffix_permutation(goal: &WindowGoal, bank: &[Int; 48]) -> Option<[usize; 48]> {
        let mut permutation = [usize::MAX; 48];
        let mut used = [false; 48];
        for &(old, value) in &goal.registers {
            if bank[old] == value { permutation[old] = old; used[old] = true; }
        }
        for &(old, value) in &goal.registers {
            if permutation[old] != usize::MAX { continue; }
            let new = (0..48).find(|&register| !used[register] && bank[register] == value)?;
            permutation[old] = new; used[new] = true;
        }
        for old in 0..48 {
            if permutation[old] == usize::MAX && !used[old] {
                permutation[old] = old; used[old] = true;
            }
        }
        for old in 0..48 {
            if permutation[old] == usize::MAX {
                let new = (0..48).find(|&register| !used[register])?;
                permutation[old] = new; used[new] = true;
            }
        }
        Some(permutation)
    }

    fn missing(goal: &WindowGoal, bank: &[Int; 48], covered: u16) -> BTreeSet<Int> {
        let mut values = BTreeSet::new();
        if goal.rename { values.extend(live_out_deficits(goal, bank).into_keys()); }
        else {
            for &(register, value) in &goal.registers {
                if bank[register] != value { values.insert(value); }
            }
        }
        for (index, &value) in goal.targets.iter().enumerate() {
            if covered & (1 << index) == 0 { values.insert(value); }
        }
        values
    }

    fn inverse_atoms(target: Int, atom: Int, limit: Int, mut offer: impl FnMut(Int)) {
        if target > atom { offer(target - atom); }
        if target <= limit - atom { offer(target + atom); }
        if atom > target { offer(atom - target); }
        if target % atom == 0 { offer(target / atom); }
        if target <= limit / atom { offer(target * atom); }
        if atom % target == 0 { offer(atom / target); }
    }

    fn state_rank(goal: &WindowGoal, bank: &[Int; 48], covered: u16, limit: Int) -> usize {
        let wanted = missing(goal, bank, covered);
        let resident: HashSet<Int> = bank.iter().copied().filter(|&v| v != 0).collect();
        let mismatched = missing_registers(goal, bank);
        let mut rank = mismatched * 40;
        for target in wanted {
            if resident.contains(&target) { rank += 65; continue; }
            let mut direct = false;
            for &atom in &resident {
                inverse_atoms(target, atom, limit, |value| direct |= resident.contains(&value));
                if direct { break; }
            }
            rank += if direct { 100 } else { 240 };
        }
        rank
    }

    fn operations(state: &WindowState, goal: &WindowGoal, limit: Int, cap: usize) -> Vec<Operation> {
        let mut locations = BTreeMap::new();
        for (register, &value) in state.bank.iter().enumerate() {
            if value != 0 { locations.entry(value).or_insert(register); }
        }
        let goals = missing(goal, &state.bank, state.covered);
        let copy_values: BTreeSet<Int> = if goal.rename {
            live_out_deficits(goal, &state.bank).into_keys().collect()
        } else {
            goal.registers.iter().filter(|&&(r, v)| state.bank[r] != v).map(|&(_, v)| v).collect()
        };
        let mut wanted: HashMap<Int, usize> = HashMap::new();
        for &target in &goals {
            let mut once = HashSet::new();
            once.insert(target);
            for &atom in locations.keys() { inverse_atoms(target, atom, limit, |value| { once.insert(value); }); }
            for value in once { *wanted.entry(value).or_default() += 1; }
        }
        let atoms: Vec<(Int, usize)> = locations.into_iter().collect();
        let mut values: BTreeMap<Int, Operation> = BTreeMap::new();
        for (position, &(a, ra)) in atoms.iter().enumerate() {
            for &(b, rb) in &atoms[..=position] {
                let mut offer = |op, value, left, right| {
                    if value <= 0 || value > limit { return; }
                    if state.bank.contains(&value) && !copy_values.contains(&value) { return; }
                    let distance = goals.iter().map(|&target| 64 - (target - value).unsigned_abs().leading_zeros()).min().unwrap_or(0);
                    let hits = wanted.get(&value).copied().unwrap_or(0) + if goals.contains(&value) { 8 } else { 0 };
                    values.entry(value).or_insert(Operation { op, a: left, b: right, value, hits, distance });
                };
                if a <= limit - b { offer(b'+', a + b, ra, rb); }
                if a > b { offer(b'-', a - b, ra, rb); }
                if a <= limit / b { offer(b'*', a * b, ra, rb); }
                if a % b == 0 { offer(b'/', a / b, ra, rb); }
                if b % a == 0 { offer(b'/', b / a, rb, ra); }
            }
        }
        let mut result: Vec<Operation> = values.into_values().collect();
        result.sort_by_key(|op| (std::cmp::Reverse(op.hits), op.distance, op.value, op.op, op.a, op.b));
        result.truncate(cap);
        result
    }

    fn destinations(bank: &[Int; 48], goal: &WindowGoal, value: Int) -> Vec<usize> {
        let mut destinations = Vec::new();
        if !goal.rename {
            for &(register, desired) in &goal.registers {
                if desired == value && bank[register] != value { destinations.push(register); }
            }
        }
        // An already resident value only needs an explicit copy/recomputation
        // when the suffix requires it in another physical register.
        if !goal.rename && bank.contains(&value) { return destinations; }
        let goal_values = missing(goal, bank, 0);
        let mut scratch = Vec::new();
        for register in 0..48 {
            let old = bank[register];
            if goal.rename {
                let required = goal.registers.iter().filter(|&&(_, desired)| desired == old).count();
                let resident = bank.iter().filter(|&&item| item == old).count();
                if old != 0 && required != 0 && resident <= required { continue; }
            } else if goal.registers.iter().any(|&(r, desired)| r == register && old == desired) { continue; }
            let duplicate = old != 0 && bank.iter().filter(|&&other| other == old).count() > 1;
            let penalty = if old == 0 { 0 } else if duplicate { 1 } else if !goal_values.contains(&old) { 2 } else { 3 };
            scratch.push((penalty, register));
        }
        scratch.sort_unstable();
        for (_, register) in scratch.into_iter().take(3) {
            if !destinations.contains(&register) { destinations.push(register); }
        }
        destinations
    }

    fn search_window(bank: [Int; 48], goal: &WindowGoal, covered: u16, maximum: usize,
                     limit: Int, config: &WindowConfig, deadline: Instant, expanded: &mut usize) -> Option<Program> {
        if finish(goal, &bank, covered) { return Some(Vec::new()); }
        let missing_registers = missing_registers(goal, &bank);
        let missing_values = missing(goal, &bank, covered).iter().filter(|value| !bank.contains(value)).count();
        if missing_registers.max(missing_values) > maximum { return None; }
        let initial = WindowState { bank, covered, program: Vec::new(), rank: 0, hash: bank_hash(&bank, covered) };
        let mut visited = HashSet::from([initial.hash]);
        let mut front = vec![initial];
        for _depth in 1..=maximum {
            let mut next = Vec::new();
            for state in &front {
                if Instant::now() >= deadline { return None; }
                let candidates = operations(state, goal, limit, config.candidates);
                for operation in candidates {
                    if *expanded & 31 == 0 && Instant::now() >= deadline { return None; }
                    for destination in destinations(&state.bank, goal, operation.value) {
                        *expanded += 1;
                        let mut candidate = state.clone();
                        candidate.bank[destination] = operation.value;
                        candidate.program.push([operation.op as i32, operation.a as i32, operation.b as i32, destination as i32]);
                        for (index, &target) in goal.targets.iter().enumerate() {
                            if target == operation.value { candidate.covered |= 1 << index; }
                        }
                        if finish(goal, &candidate.bank, candidate.covered) { return Some(candidate.program); }
                        candidate.hash = bank_hash(&candidate.bank, candidate.covered);
                        if !visited.insert(candidate.hash) { continue; }
                        candidate.rank = state_rank(goal, &candidate.bank, candidate.covered, limit);
                        next.push(candidate);
                    }
                }
            }
            next.sort_by_key(|state| (state.rank, state.hash));
            next.truncate(config.beam);
            if next.is_empty() { return None; }
            front = next;
        }
        None
    }

    struct Replay { banks: Vec<[Int; 48]>, results: Vec<Int>, live: Vec<u64> }

    fn replay(puzzle: &Puzzle, program: &Program) -> Outcome<Replay> {
        let mut checked = puzzle.clone(); checked.load_hint(program)?;
        let mut bank = [0; 48]; bank[..puzzle.starts.len()].copy_from_slice(&puzzle.starts);
        let mut banks = vec![bank]; let mut results = Vec::new();
        for &[op, a, b, destination] in program {
            let value = apply(op as u8, bank[a as usize], bank[b as usize])?;
            bank[destination as usize] = value; results.push(value); banks.push(bank);
        }
        let mut live = vec![0u64; program.len() + 1];
        for i in (0..program.len()).rev() {
            let [_, a, b, destination] = program[i];
            live[i] = (live[i + 1] & !(1 << destination)) | (1 << a) | (1 << b);
        }
        Ok(Replay { banks, results, live })
    }

    fn window_goal(puzzle: &Puzzle, replay: &Replay, start: usize, end: usize, rename: bool) -> (WindowGoal, u16) {
        let registers = (0..48).filter(|&r| replay.live[end] & (1 << r) != 0)
            .map(|r| (r, replay.banks[end][r])).collect();
        let targets: Vec<Int> = replay.results[start..end].iter().copied()
            .filter(|value| puzzle.targets.contains(value)).collect::<BTreeSet<_>>().into_iter().collect();
        let mut covered = 0;
        for (index, value) in targets.iter().enumerate() {
            if puzzle.starts.contains(value) || replay.results[..start].contains(value) { covered |= 1 << index; }
        }
        let all = (1u16 << targets.len()) - 1;
        (WindowGoal { registers, targets, all, rename }, covered)
    }

    fn splice_window(program: &Program, start: usize, end: usize, replacement: &Program,
                     initial: [Int; 48], goal: &WindowGoal, limit: Int) -> Outcome<Program> {
        let mut bank = initial;
        for &[op, a, b, destination] in replacement {
            if !(0..48).contains(&a) || !(0..48).contains(&b) || !(0..48).contains(&destination) {
                return Err("invalid replacement register".into());
            }
            let (left, right) = (bank[a as usize], bank[b as usize]);
            if left <= 0 || right <= 0 || (op as u8 == b'/' && left % right != 0) {
                return Err("invalid replacement arithmetic".into());
            }
            let value = apply(op as u8, left, right)?;
            if !(1..=limit).contains(&value) { return Err("replacement value out of bounds".into()); }
            bank[destination as usize] = value;
        }
        let permutation = if goal.rename {
            suffix_permutation(goal, &bank).ok_or("live-out values have no injective register matching")?
        } else { std::array::from_fn(|register| register) };
        let mut candidate = program[..start].to_vec();
        candidate.extend_from_slice(replacement);
        for &[op, a, b, destination] in &program[end..] {
            candidate.push([op, permutation[a as usize] as i32, permutation[b as usize] as i32,
                permutation[destination as usize] as i32]);
        }
        Ok(candidate)
    }

    fn optimize_window(puzzle: &Puzzle, hint: &Program, config: &WindowConfig) -> Outcome<Program> {
        let deadline = Instant::now() + Duration::from_millis(config.milliseconds);
        let mut best = hint.clone();
        let mut attempts = 0usize; let mut expanded = 0usize; let mut improvements = 0usize;
        let mut generator = Mt19937_64::new(config.seed);
        loop {
            let replayed = replay(puzzle, &best)?;
            if attempts >= config.windows || Instant::now() >= deadline { break; }
            let mut windows = Vec::new();
            'enumerate: for length in 3..=8usize.min(best.len()) {
                for start in 0..=best.len() - length {
                    if start & 31 == 0 && Instant::now() >= deadline { break 'enumerate; }
                    let (goal, covered) = window_goal(puzzle, &replayed, start, start + length, config.rename);
                    let lower = missing_registers(&goal, &replayed.banks[start])
                        .max(missing(&goal, &replayed.banks[start], covered).iter().filter(|v| !replayed.banks[start].contains(v)).count());
                    if lower < length {
                        windows.push((std::cmp::Reverse(length - lower), std::cmp::Reverse(goal.targets.len()), length, start));
                    }
                }
            }
            generator.shuffle(&mut windows);
            windows.sort_by_key(|item| (item.0, item.1));
            let mut improved = false;
            for (_, _, length, start) in windows {
                if attempts >= config.windows || Instant::now() >= deadline { break; }
                attempts += 1;
                let end = start + length;
                let (goal, covered) = window_goal(puzzle, &replayed, start, end, config.rename);
                if let Some(replacement) = search_window(replayed.banks[start], &goal, covered, length - 1,
                                                         puzzle.limit, config, deadline, &mut expanded) {
                    let candidate = splice_window(&best, start, end, &replacement,
                        replayed.banks[start], &goal, puzzle.limit)?;
                    let mut checked = puzzle.clone();
                    if candidate.len() < best.len() && checked.load_hint(&candidate).is_ok() {
                        best = candidate; improved = true; improvements += 1; break;
                    }
                }
            }
            if !improved { break; }
        }
        if env::var("SANTA2026_WINDOW_TRACE").map_or(false, |value| value != "0") {
            eprintln!("window_trace windows={} expanded={} improvements={}", attempts, expanded, improvements);
        }
        Ok(best)
    }

    pub fn run_window() -> Outcome<()> {
        fn parameter<T: std::str::FromStr>(args: &[String], i: usize, default: T) -> Outcome<T> {
            args.get(i).map_or(Ok(default), |value| value.parse().map_err(|_| "invalid window argument".into()))
        }
        let args: Vec<String> = env::args().collect();
        let rename_setting = args.get(6).cloned().unwrap_or_else(||
            env::var("SANTA2026_WINDOW_RENAME").unwrap_or_else(|_| "0".into()));
        let rename = match rename_setting.as_str() {
            "0" => false, "1" => true,
            _ => return Err("window rename option must be 0 or 1".into()),
        };
        let config = WindowConfig { seed: parameter(&args, 1, 0)?, windows: parameter(&args, 2, 24)?,
            beam: parameter(&args, 3, 32)?, candidates: parameter(&args, 4, 96)?, milliseconds: parameter(&args, 5, 2000)?, rename };
        if config.windows > 512 || config.beam == 0 || config.beam > 256 || config.candidates == 0 ||
            config.candidates > 512 || config.milliseconds == 0 || config.milliseconds > 120_000 {
            return Err("invalid window budget".into());
        }
        let stdin = io::stdin(); let stdout = io::stdout();
        let mut scanner = Scanner::new(BufReader::new(stdin.lock())); let mut writer = BufWriter::new(stdout.lock());
        while let Some(id) = scanner.next()? {
            let limit: Int = scanner.integer()?; let ns: usize = scanner.integer()?; let nt: usize = scanner.integer()?;
            if !(1..=1_000_000_000_000_000_000).contains(&limit) || ns == 0 || ns > 48 || nt > 4096 {
                return Err("invalid window puzzle dimensions".into());
            }
            let mut starts = Vec::new(); let mut targets = Vec::new();
            for _ in 0..ns { starts.push(scanner.integer()?); } for _ in 0..nt { targets.push(scanner.integer()?); }
            let length: usize = scanner.integer()?; if length > 4096 { return Err("invalid window hint length".into()); }
            let mut hint = Vec::new();
            for _ in 0..length {
                let op = scanner.next()?.ok_or("truncated window hint")?;
                if op.len() != 1 { return Err("invalid window operator".into()); }
                hint.push([op.as_bytes()[0] as i32, scanner.integer()?, scanner.integer()?, scanner.integer()?]);
            }
            let puzzle = Puzzle { limit, starts, targets, definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            let best = optimize_window(&puzzle, &hint, &config)?;
            write!(writer, "{} [", id).map_err(|e| e.to_string())?;
            for (i, instruction) in best.iter().enumerate() {
                if i > 0 { write!(writer, ",").map_err(|e| e.to_string())?; }
                write!(writer, "[\"{}\",{},{},{}]", instruction[0] as u8 as char, instruction[1], instruction[2], instruction[3]).map_err(|e| e.to_string())?;
            }
            writeln!(writer, "]").map_err(|e| e.to_string())?; writer.flush().map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    #[cfg(test)]
    mod window_tests {
        use super::*;

        #[test]
        fn jointly_rewrites_two_physical_live_outs_without_a_single_root_gain() {
            let mut bank = [0; 48]; bank[0] = 2; bank[1] = 5;
            let goal = WindowGoal { registers: vec![(4, 14), (6, 35)], targets: vec![], all: 0, rename: false };
            let config = WindowConfig { seed: 0, windows: 1, beam: 32, candidates: 96, milliseconds: 2000, rename: false };
            let mut expanded = 0;
            let program = search_window(bank, &goal, 0, 4, 100, &config,
                                        Instant::now() + Duration::from_secs(2), &mut expanded).unwrap();
            assert_eq!(program.len(), 3);
            for [op, a, b, d] in program { bank[d as usize] = apply(op as u8, bank[a as usize], bank[b as usize]).unwrap(); }
            assert_eq!((bank[4], bank[6]), (14, 35));
        }

        #[test]
        fn renaming_allows_a_shorter_window_and_rewrites_suffix_reads_and_writes() {
            let puzzle = Puzzle { limit: 100, starts: vec![2,5], targets: vec![10,12,24],
                definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            let hint = vec![[47,0,0,2],[42,0,2,3],[42,1,2,4],
                [42,3,4,6],[43,6,3,4],[42,4,3,6]];
            let old = replay(&puzzle, &hint).unwrap();
            let (fixed, covered) = window_goal(&puzzle, &old, 0, 3, false);
            let mut config = WindowConfig { seed: 0, windows: 1, beam: 32, candidates: 96,
                milliseconds: 2000, rename: false };
            assert!(search_window(old.banks[0], &fixed, covered, 2, puzzle.limit, &config,
                Instant::now() + Duration::from_secs(2), &mut 0).is_none());
            config.rename = true;
            let (flexible, covered) = window_goal(&puzzle, &old, 0, 3, true);
            let replacement = search_window(old.banks[0], &flexible, covered, 2, puzzle.limit, &config,
                Instant::now() + Duration::from_secs(2), &mut 0).unwrap();
            assert!(replacement.is_empty());
            let candidate = splice_window(&hint, 0, 3, &replacement, old.banks[0], &flexible, puzzle.limit).unwrap();
            assert_eq!(candidate, vec![[42,0,1,6],[43,6,0,1],[42,1,0,6]]);
            let mut checked = puzzle.clone(); checked.load_hint(&candidate).unwrap();
        }

        #[test]
        fn equal_live_outs_require_distinct_physical_copies() {
            let goal = WindowGoal { registers: vec![(3,2),(4,2),(5,5)], targets: vec![], all: 0, rename: true };
            let mut bank = [0;48]; bank[0] = 2; bank[1] = 5;
            assert!(!finish(&goal, &bank, 0));
            assert_eq!(missing_registers(&goal, &bank), 1);
            assert!(suffix_permutation(&goal, &bank).is_none());
            bank[2] = 2;
            let permutation = suffix_permutation(&goal, &bank).unwrap();
            assert_ne!(permutation[3], permutation[4]);
            assert_eq!(bank[permutation[3]], 2); assert_eq!(bank[permutation[4]], 2);
            assert_eq!(permutation.iter().copied().collect::<BTreeSet<_>>(), (0..48).collect());
            assert!(finish(&goal, &bank, 0));
        }

        #[test]
        fn aliased_live_outs_pay_for_a_copy_before_divergent_suffix_writes() {
            let puzzle = Puzzle { limit: 100, starts: vec![1,2,5], targets: vec![7,14],
                definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            let hint = vec![[42,1,0,3],[42,1,0,4],[42,2,0,6],[43,3,2,3],[42,4,3,5]];
            let old = replay(&puzzle, &hint).unwrap();
            let (goal, covered) = window_goal(&puzzle, &old, 0, 3, true);
            assert_eq!(missing_registers(&goal, &old.banks[0]), 1);
            let config = WindowConfig { seed: 0, windows: 1, beam: 32, candidates: 96,
                milliseconds: 2000, rename: true };
            let replacement = search_window(old.banks[0], &goal, covered, 1, puzzle.limit, &config,
                Instant::now() + Duration::from_secs(2), &mut 0).unwrap();
            assert_eq!(replacement.len(), 1);
            let candidate = splice_window(&hint, 0, 3, &replacement, old.banks[0], &goal, puzzle.limit).unwrap();
            assert_eq!(candidate.len(), 3);
            // The suffix overwrites one copy before reading the other one.
            // Mapping both old registers to a single new slot would produce49.
            let mut checked = puzzle.clone(); checked.load_hint(&candidate).unwrap();
        }

        #[test]
        fn renamed_outputs_do_not_forget_overwritten_target_coverage() {
            let goal = WindowGoal { registers: vec![(4,7)], targets: vec![4,7], all: 3, rename: true };
            let mut bank = [0;48]; bank[0] = 2; bank[1] = 5;
            bank[2] = 4; // The first target appeared and was then overwritten.
            bank[2] = 7;
            assert!(!finish(&goal, &bank, 2));
            assert!(finish(&goal, &bank, 3));
        }
    }
}

fn main() {
    if let Err(error) = engine::run_window() { eprintln!("Window search error: {}", error); std::process::exit(1); }
}
