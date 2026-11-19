// SPDX-License-Identifier: Apache-2.0
// Fresh, puzzle-only real-register construction for Santa 2026.
// Exact short joins, residual-rate planning and scale/target-anchor eviction
// are inspired by Pablo Diego's Apache-2.0 Deterministic Atlas Solver, v1:
// https://www.kaggle.com/code/pablodiegoo/santa-2026-deterministic-atlas-solver?scriptVersionId=371078087
// This independent Rust implementation also reuses Tony Li / konbu17's integer
// index and expression representation through repair.rs. See THIRD_PARTY.md,
// licenses/Apache-2.0.txt and the attribution in repair.rs.
// CLI: construct-rust [seed=0] [effort=1] [milliseconds=2000]
// Input: id limit ns nt starts... targets... (no hint). Output: id JSON-program.

#[allow(dead_code)]
mod engine {
    include!("repair.rs");
    use std::time::{Duration, Instant};

    #[derive(Clone, Copy, Debug)]
    struct Step { op: u8, a: Int, b: Int, value: Int }

    #[derive(Clone, Default)]
    struct Plan { steps: Vec<Step>, available: BTreeSet<Int>, value: Int }

    impl Plan {
        fn new(bank: &[Int; 48]) -> Self {
            Self { steps: Vec::new(), available: bank.iter().copied().filter(|&v| v > 0).collect(), value: 0 }
        }
        fn expression(&mut self, dictionary: &Dictionary, id: i32) -> Outcome<Int> {
            let expression = dictionary.expressions[id as usize];
            if self.available.contains(&expression.value) { self.value = expression.value; return Ok(expression.value); }
            if expression.a < 0 || expression.b < 0 { return Err("missing resident expression leaf".into()); }
            let a = self.expression(dictionary, expression.a)?;
            let b = self.expression(dictionary, expression.b)?;
            self.push(expression.op, a, b, dictionary.limit)
        }
        fn push(&mut self, op: u8, a: Int, b: Int, limit: Int) -> Outcome<Int> {
            let value = checked(op, a, b, limit)?;
            if self.available.insert(value) { self.steps.push(Step { op, a, b, value }); }
            self.value = value;
            Ok(value)
        }
    }

    fn checked(op: u8, a: Int, b: Int, limit: Int) -> Outcome<Int> {
        if a <= 0 || b <= 0 { return Err("undefined constructor source".into()); }
        if op == b'/' && a % b != 0 { return Err("inexact constructor division".into()); }
        if op == b'*' && a > limit / b { return Err("constructor product exceeds limit".into()); }
        let value = apply(op, a, b)?;
        if value <= 0 || value > limit { return Err("constructor value exceeds puzzle limit".into()); }
        Ok(value)
    }

    fn mix64(mut value: u64) -> u64 {
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d049bb133111eb);
        value ^ (value >> 31)
    }

    fn expression_nodes(dictionary: &Dictionary, id: i32, resident: &BTreeSet<Int>, nodes: &mut Vec<Int>) {
        let expression = dictionary.expressions[id as usize];
        if resident.contains(&expression.value) || nodes.contains(&expression.value) { return; }
        if expression.a >= 0 {
            expression_nodes(dictionary, expression.a, resident, nodes);
            expression_nodes(dictionary, expression.b, resident, nodes);
        }
        nodes.push(expression.value);
    }

    #[derive(Clone)]
    struct Machine {
        limit: Int,
        starts: Vec<Int>,
        targets: Vec<Int>,
        bank: [Int; 48],
        age: [usize; 48],
        covered: BTreeSet<Int>,
        program: Program,
        seed: u64,
        effort: usize,
        started: Instant,
        duration: Duration,
    }

    impl Machine {
        fn new(limit: Int, starts: Vec<Int>, targets: Vec<Int>, seed: u64,
               effort: usize, milliseconds: u64) -> Outcome<Self> {
            if !(1..=1_000_000_000_000_000_000).contains(&limit) || starts.is_empty() || starts.len() > 48
                || targets.len() > 4096 || starts.iter().chain(&targets).any(|&v| v <= 0 || v > limit) {
                return Err("invalid constructor puzzle".into());
            }
            let mut bank = [0; 48]; bank[..starts.len()].copy_from_slice(&starts);
            let covered = starts.iter().copied().collect();
            Ok(Self { limit, starts, targets, bank, age: [0; 48], covered, program: Vec::new(), seed,
                effort, started: Instant::now(), duration: Duration::from_millis(milliseconds) })
        }
        fn planning(&self) -> bool { self.started.elapsed() < self.duration }
        fn seed_key(&self, salt: u64, value: Int) -> u64 {
            if self.seed == 0 { value as u64 }
            else { mix64(self.seed ^ salt ^ mix64(value as u64)) }
        }
        fn plan_key(&self, plan: &Plan) -> u64 {
            plan.steps.iter().fold(self.seed, |hash, step| {
                mix64(hash ^ step.op as u64 ^ mix64(step.a as u64)
                    ^ mix64(step.b as u64).rotate_left(17) ^ (step.value as u64).rotate_left(31))
            })
        }
        fn register(&self, value: Int) -> Option<usize> { self.bank.iter().position(|&v| v == value) }
        fn missing(&self) -> Vec<Int> {
            self.targets.iter().copied().filter(|v| !self.covered.contains(v)).collect::<BTreeSet<_>>().into_iter().collect()
        }
        fn victim(&self, pinned: u64) -> Outcome<usize> {
            if let Some(index) = (0..48).find(|&i| self.bank[i] == 0 && pinned >> i & 1 == 0) { return Ok(index); }
            let mut bands = [0usize; 17];
            for &value in &self.bank {
                if value > 0 { bands[((64 - (value as u64).leading_zeros()) / 4).min(16) as usize] += 1; }
            }
            let missing = self.missing();
            let cap = 2 - (self.seed as usize % 2);
            let mut selected = None;
            let mut lowest = f64::INFINITY;
            for index in 0..48 {
                if pinned >> index & 1 != 0 { continue; }
                let value = self.bank[index];
                if value == 1 && self.bank.iter().filter(|&&v| v == 1).count() == 1 { continue; }
                if self.bank.iter().enumerate().any(|(other, &v)| other != index && v == value) { return Ok(index); }
                let band = ((64 - (value as u64).leading_zeros()) / 4).min(16) as usize;
                let mut utility = if value <= 64 { 2500.0 } else if value <= 4096 { 700.0 } else { 0.0 };
                if [4, 16, 256, 65536, 4294967296].contains(&value) { utility += 1800.0; }
                if bands[band] <= cap { utility += 3200.0; }
                else { utility -= (bands[band] - cap) as f64 * 700.0; }
                if index < self.starts.len() { utility += 200.0; }
                for &target in &missing {
                    let difference = (target - value).abs();
                    if difference <= 50_000 || difference as i128 * 128 <= target as i128 { utility += 9000.0; }
                    else {
                        let quotient = (target + value / 2) / value;
                        if (2..=64).contains(&quotient)
                            && (target as i128 - quotient as i128 * value as i128).abs() <= 50_000 { utility += 7000.0; }
                    }
                }
                utility += self.age[index] as f64 * 0.01;
                if utility < lowest { lowest = utility; selected = Some(index); }
            }
            selected.ok_or_else(|| "constructor register pressure exceeds 48".into())
        }
        fn emit(&mut self, op: u8, a: usize, b: usize, pinned: u64) -> Outcome<usize> {
            let value = checked(op, self.bank[a], self.bank[b], self.limit)?;
            if let Some(existing) = self.register(value) { self.covered.insert(value); return Ok(existing); }
            if self.program.len() >= 4096 { return Err("constructor instruction budget exhausted".into()); }
            let destination = self.victim(pinned)?;
            self.program.push([op as i32, a as i32, b as i32, destination as i32]);
            self.bank[destination] = value;
            self.covered.insert(value);
            self.age[a] = self.program.len(); self.age[b] = self.program.len(); self.age[destination] = self.program.len();
            Ok(destination)
        }
        fn execute(&mut self, plan: &Plan, pinned: u64) -> Outcome<usize> {
            let mut uses: BTreeMap<Int, usize> = BTreeMap::new();
            for step in &plan.steps { *uses.entry(step.a).or_default() += 1; *uses.entry(step.b).or_default() += 1; }
            for step in &plan.steps {
                let a = self.register(step.a).ok_or_else(|| "evicted planned left operand".to_string())?;
                let b = self.register(step.b).ok_or_else(|| "evicted planned right operand".to_string())?;
                *uses.get_mut(&step.a).unwrap() -= 1; *uses.get_mut(&step.b).unwrap() -= 1;
                let mut locked = pinned;
                for index in 0..48 {
                    if uses.get(&self.bank[index]).copied().unwrap_or(0) > 0 { locked |= 1u64 << index; }
                }
                let destination = self.emit(step.op, a, b, locked)?;
                if self.bank[destination] != step.value { return Err("constructor plan arithmetic mismatch".into()); }
            }
            self.register(plan.value).ok_or_else(|| "missing constructed plan result".into())
        }
        fn ensure_unit(&mut self, pinned: u64) -> Outcome<usize> {
            if let Some(index) = self.register(1) { return Ok(index); }
            let source = self.bank.iter().position(|&v| v > 0).ok_or_else(|| "empty constructor bank".to_string())?;
            self.emit(b'/', source, source, pinned)
        }
        fn one_operation(&self, target: Int) -> Option<(u8, usize, usize)> {
            for b in 0..48 {
                let atom = self.bank[b];
                if atom <= 0 { continue; }
                for (value, op, left_first) in inverses(target, atom, self.limit) {
                    if let Some(a) = self.register(value) {
                        return Some(if left_first { (op, a, b) } else { (op, b, a) });
                    }
                }
            }
            None
        }
        fn binary(&mut self, target: Int, pinned: u64) -> Outcome<usize> {
            if let Some(index) = self.register(target) { return Ok(index); }
            let unit = self.ensure_unit(pinned)?;
            let mut current = unit;
            let mut shifts = 63 - (target as u64).leading_zeros();
            // A resident binary prefix is an actual reusable anchor.
            for shift in 0..shifts {
                if let Some(index) = self.register(target >> shift) { current = index; shifts = shift; break; }
            }
            for shift in (0..shifts).rev() {
                current = self.emit(b'+', current, current, pinned)?;
                if target >> shift & 1 != 0 {
                    let unit = self.ensure_unit(pinned | (1u64 << current))?;
                    current = self.emit(b'+', current, unit, pinned)?;
                }
            }
            Ok(current)
        }
        fn seed_palette(&mut self) -> Outcome<()> {
            if self.missing().is_empty() { return Ok(()); }
            self.ensure_unit(0)?;
            let maximum = *self.targets.iter().max().unwrap_or(&1);
            let palette = (self.seed % 4 + 1) % 4;
            let mut goals = vec![2];
            if palette != 0 { goals.extend([3, 4, 16, 256, 65536]); }
            if palette == 2 { goals.extend([5, 8, 10, 4294967296]); }
            if palette == 3 { goals.extend([8, 32, 1024, 1048576, 4294967296]); }
            if palette != 0 && maximum >= 10_000_000_000_000 { goals.push(4294967296); }
            if self.seed != 0 && maximum >= 31 {
                // A full-seed helper adds arithmetic diversity, not just a
                // permutation of otherwise identical register instructions.
                goals.push(3 + 2 * (mix64(self.seed ^ 0x636f6e7374727563) % 15) as Int);
            }
            for value in goals {
                if value <= self.limit && value <= maximum && self.register(value).is_none() {
                    if let Some((op, a, b)) = self.one_operation(value) { self.emit(op, a, b, 0)?; }
                    else { self.binary(value, 0)?; }
                }
            }
            Ok(())
        }
        fn dictionary(&self, deep: bool) -> Dictionary {
            let cap = if deep { (40_000 * self.effort).min(160_000) } else { 0 };
            let size = (cap.max(4096) * 2).next_power_of_two();
            let mut dictionary = Dictionary::new(self.limit, size.trailing_zeros() as usize);
            let mut leaves: Vec<Int> = self.bank.iter().copied().filter(|&v| v > 0).collect::<BTreeSet<_>>().into_iter().collect();
            if self.seed != 0 { leaves.sort_by_key(|&value| self.seed_key(0x6c656166, value)); }
            for value in leaves { dictionary.add(value, -1, -1, 0, 0); }
            dictionary.n0 = dictionary.expressions.len();
            for i in 0..dictionary.n0 { for j in 0..=i { dictionary.combine(i, j, 1); } }
            dictionary.n1 = dictionary.expressions.len();
            if deep {
                let mut first: Vec<usize> = (dictionary.n0..dictionary.n1).collect();
                first.sort_by_key(|&id| dictionary.expressions[id].value);
                let stride = 29 + 2 * (self.seed as usize % 3);
                let mut phases: Vec<usize> = (0..stride.min(first.len())).collect();
                if self.seed != 0 { Mt19937_64::new(self.seed ^ 0x706861736573).shuffle(&mut phases); }
                'phases: for phase in phases {
                    for offset in (phase..first.len()).step_by(stride) {
                        for leaf in 0..dictionary.n0 {
                            dictionary.combine(first[offset], leaf, 2);
                            if dictionary.expressions.len() >= cap { break 'phases; }
                        }
                    }
                }
            }
            for (id, expression) in dictionary.expressions.iter().enumerate() {
                dictionary.sorted[expression.cost as usize].push((expression.value, id as i32));
            }
            for group in &mut dictionary.sorted { group.sort_unstable(); }
            dictionary
        }
        fn exact(&self, dictionary: &Dictionary, target: Int, maximum: usize) -> Outcome<Option<Plan>> {
            let direct = dictionary.index.get(target);
            if direct >= 0 {
                let mut plan = Plan::new(&self.bank); plan.expression(dictionary, direct)?;
                if plan.steps.len() <= maximum { return Ok(Some(plan)); }
            }
            if maximum < 3 { return Ok(None); }
            let mut best: Option<Plan> = None;
            for id in 0..dictionary.n1 {
                if id % 64 == 0 && !self.planning() { break; }
                let right = dictionary.expressions[id];
                for (value, op, left_first) in inverses(target, right.value, self.limit) {
                    let left = dictionary.index.get(value);
                    if left < 0 { continue; }
                    if dictionary.expressions[left as usize].cost + right.cost + 1 > maximum as i32 { continue; }
                    let mut plan = Plan::new(&self.bank);
                    let a = plan.expression(dictionary, left)?;
                    let b = plan.expression(dictionary, id as i32)?;
                    if left_first { plan.push(op, a, b, self.limit)?; } else { plan.push(op, b, a, self.limit)?; }
                    if best.as_ref().is_none_or(|previous| plan.steps.len() < previous.steps.len()
                        || (self.seed != 0 && plan.steps.len() == previous.steps.len()
                            && self.plan_key(&plan) < self.plan_key(previous))) { best = Some(plan); }
                }
            }
            Ok(best)
        }
        fn approximate(&self, dictionary: &Dictionary, target: Int) -> Vec<Approximation> {
            let mut candidates = Vec::new();
            let mut seen: BTreeMap<Int, usize> = BTreeMap::new();
            let resident: BTreeSet<Int> = self.bank.iter().copied().filter(|&value| value > 0).collect();
            let mut offer = |a: i32, b: i32, op: u8| {
                let left = dictionary.expressions[a as usize];
                let result = if b < 0 { Ok(left.value) } else { checked(op, left.value, dictionary.expressions[b as usize].value, self.limit) };
                let Ok(value) = result else { return; };
                let residual = (target - value).abs();
                if residual >= target { return; }
                // Count exactly the nodes Plan will emit. Two P2 expressions
                // may share their P1 helper, giving four real operations even
                // though their nominal tree cost is five.
                let mut nodes = Vec::with_capacity(5);
                expression_nodes(dictionary, a, &resident, &mut nodes);
                if b >= 0 { expression_nodes(dictionary, b, &resident, &mut nodes); }
                if !resident.contains(&value) && !nodes.contains(&value) { nodes.push(value); }
                let cost = nodes.len();
                let final_cost = cost + usize::from(residual != 0);
                let rate = ((target as f64 + 1.0).log2() - (residual as f64 + 1.0).log2()) / final_cost.max(1) as f64;
                let candidate = Approximation { a, b, op, value, residual, rate, cost };
                if let Some(&index) = seen.get(&value) {
                    let previous: &Approximation = &candidates[index];
                    let tie = |item: &Approximation| self.seed_key(0x617070726f78,
                        item.a as Int ^ (item.b as Int).wrapping_shl(32) ^ item.op as Int);
                    if cost < previous.cost || (self.seed != 0 && cost == previous.cost && tie(&candidate) < tie(previous)) {
                        candidates[index] = candidate;
                    }
                } else {
                    seen.insert(value, candidates.len()); candidates.push(candidate);
                }
            };
            for group in &dictionary.sorted {
                let position = lower_bound(group, target) as isize;
                for offset in -4..=3 {
                    let index = position + offset;
                    if index >= 0 && (index as usize) < group.len() { offer(group[index as usize].1, -1, b'+'); }
                }
            }
            let atoms = &dictionary.sorted[1];
            let stride = (atoms.len() / (96 * self.effort)).max(1);
            let mut factors: Vec<i32> = (0..dictionary.n0).map(|id| id as i32).collect();
            factors.extend(atoms.iter().step_by(stride).map(|&(_, id)| id));
            for right in factors {
                let value = dictionary.expressions[right as usize].value;
                for group in 1..3 {
                    let values = &dictionary.sorted[group];
                    for (center, op) in [(target / value, b'*'), (target - value, b'+'), (target + value, b'-')] {
                        let position = lower_bound(values, center) as isize;
                        // lower_bound(floor(t/v)) can point at the floor itself;
                        // also inspect the first value strictly above it.
                        for offset in -1..=1 {
                            let index = position + offset;
                            if index >= 0 && (index as usize) < values.len() { offer(values[index as usize].1, right, op); }
                        }
                    }
                }
            }
            // Dense P2+P2 sums are joined without allocating the quadratic pool.
            let values = &dictionary.sorted[2];
            if values.len() >= 2 {
                let (mut left, mut right) = (0usize, values.len() - 1);
                let mut closest = (Int::MAX, 0usize, 0usize);
                while left <= right {
                    let sum = values[left].0 + values[right].0;
                    let distance = (sum - target).abs();
                    if distance < closest.0 { closest = (distance, left, right); }
                    if sum == target {
                        offer(values[left].1, values[right].1, b'+');
                        left += 1;
                        if right > 0 { right -= 1; } else { break; }
                    } else if sum < target { left += 1; } else if right > 0 { right -= 1; } else { break; }
                }
                offer(values[closest.1].1, values[closest.2].1, b'+');
            }
            candidates.sort_by(|a, b| b.rate.total_cmp(&a.rate).then(a.residual.cmp(&b.residual))
                .then(self.seed_key(0x72617465, a.value).cmp(&self.seed_key(0x72617465, b.value))));
            candidates.truncate(12 + 4 * self.effort);
            candidates
        }
        fn solve_target(&mut self, target: Int, depth: usize, pinned: u64, prepared: Option<&Dictionary>) -> Outcome<usize> {
            if let Some(index) = self.register(target) { return Ok(index); }
            if !self.planning() || depth >= 10 { return self.binary(target, pinned); }
            let rebuilt;
            let dictionary = if let Some(dictionary) = prepared { dictionary } else {
                rebuilt = self.dictionary(depth < 2); &rebuilt
            };
            if let Some(plan) = self.exact(dictionary, target, if depth == 0 { 4 } else { 3 })? {
                return self.execute(&plan, pinned);
            }
            let candidates = self.approximate(dictionary, target);
            let mut selected: Option<(f64, Approximation, Plan)> = None;
            for candidate in candidates {
                let mut plan = Plan::new(&self.bank);
                let a = plan.expression(dictionary, candidate.a)?;
                if candidate.b >= 0 {
                    let b = plan.expression(dictionary, candidate.b)?;
                    plan.push(candidate.op, a, b, self.limit)?;
                }
                let residual = dictionary.index.get(candidate.residual);
                let future_cost = if candidate.residual == 0 { 0.0 }
                                  else if residual >= 0 { dictionary.expressions[residual as usize].cost as f64 }
                                  else { (candidate.residual as f64 + 1.0).log2() * 0.35 };
                let score = plan.steps.len() as f64 + f64::from(candidate.residual != 0)
                    + future_cost - candidate.rate * 0.08;
                if selected.as_ref().is_none_or(|previous| score < previous.0
                    || (self.seed != 0 && score == previous.0 && self.plan_key(&plan) < self.plan_key(&previous.2))) {
                    selected = Some((score, candidate, plan));
                }
            }
            let Some((_, candidate, plan)) = selected else { return self.binary(target, pinned); };
            let anchor = self.execute(&plan, pinned)?;
            if candidate.residual == 0 { return Ok(anchor); }
            let residual = self.solve_target(candidate.residual, depth + 1, pinned | (1u64 << anchor), None)?;
            let anchor = self.register(candidate.value).ok_or_else(|| "evicted protected target anchor".to_string())?;
            if candidate.value < target { self.emit(b'+', anchor, residual, pinned) }
            else { self.emit(b'-', anchor, residual, pinned) }
        }
        fn future_estimate(&self, targets: &[Int]) -> f64 {
            let known: BTreeSet<Int> = self.bank.iter().copied().filter(|&v| v > 0).collect();
            let mut total = 0.0;
            for &target in targets {
                if self.covered.contains(&target) { continue; }
                if known.iter().any(|&a| known.contains(&(target - a)) || known.contains(&(target + a))
                    || (target % a == 0 && known.contains(&(target / a)))) { total += 1.0; }
                else {
                    let distance = known.iter().map(|&a| (target - a).abs()).min().unwrap_or(target);
                    total += 2.0 + (distance as f64 + 1.0).log2() * 0.25;
                }
            }
            total
        }
        fn solve(mut self) -> Outcome<Program> {
            self.seed_palette()?;
            while !self.missing().is_empty() {
                let missing = self.missing();
                if !self.planning() { for target in missing { self.binary(target, 0)?; } break; }
                let dictionary = self.dictionary(true);
                let mut options = Vec::new();
                for &target in &missing {
                    let id = dictionary.index.get(target);
                    if id >= 0 {
                        let mut plan = Plan::new(&self.bank); plan.expression(&dictionary, id)?;
                        options.push((target, plan));
                    }
                }
                if options.is_empty() {
                    for &target in &missing {
                        if let Some(plan) = self.exact(&dictionary, target, 4)? { options.push((target, plan)); }
                        if !self.planning() { break; }
                    }
                }
                if !options.is_empty() {
                    options.sort_by_key(|(target, plan)| (plan.steps.len(), self.seed_key(0x746172676574, *target)));
                    options.truncate(2 + self.effort.min(4));
                    let mut selected = None;
                    for (target, plan) in options {
                        let mut after = self.clone();
                        if after.execute(&plan, 0).is_err() { continue; }
                        let own = after.program.len() - self.program.len();
                        let weight = [0.15, 0.30, 0.45][self.seed as usize % 3];
                        let score = own as f64 + weight * after.future_estimate(&missing);
                        if selected.as_ref().is_none_or(|&(previous, previous_target, _)| score < previous
                            || (self.seed != 0 && score == previous
                                && self.seed_key(0x746172676574, target) < self.seed_key(0x746172676574, previous_target))) {
                            selected = Some((score, target, plan));
                        }
                    }
                    if let Some((_, _, plan)) = selected { self.execute(&plan, 0)?; continue; }
                }
                let goal = missing.iter().copied().min_by(|&a, &b| {
                    let estimate = |target: Int| {
                        let group = &dictionary.sorted[2];
                        let position = lower_bound(group, target);
                        let distance = [position.checked_sub(1), (position < group.len()).then_some(position)].into_iter()
                            .flatten().map(|i| (target - group[i].0).abs()).min().unwrap_or(target);
                        (distance as f64 + 1.0).log2() + (target as f64 + 1.0).log2() * 0.15
                    };
                    estimate(a).total_cmp(&estimate(b))
                        .then(self.seed_key(0x676f616c, a).cmp(&self.seed_key(0x676f616c, b)))
                }).unwrap();
                self.solve_target(goal, 0, 0, Some(&dictionary))?;
            }
            let program = prune_dead(&self.starts, &self.targets, &self.program, self.limit)?;
            let mut replay = Puzzle { limit: self.limit, starts: self.starts, targets: self.targets,
                definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
            replay.load_hint(&program)?;
            Ok(program)
        }
    }

    #[derive(Clone, Copy)]
    struct Approximation { a: i32, b: i32, op: u8, value: Int, residual: Int, rate: f64, cost: usize }

    fn inverses(target: Int, atom: Int, limit: Int) -> Vec<(Int, u8, bool)> {
        let mut out = Vec::with_capacity(6);
        if target > atom { out.push((target - atom, b'+', true)); }
        if target <= limit - atom { out.push((target + atom, b'-', true)); }
        if atom > target { out.push((atom - target, b'-', false)); }
        if target % atom == 0 { out.push((target / atom, b'*', true)); }
        if target <= limit / atom { out.push((target * atom, b'/', true)); }
        if atom % target == 0 { out.push((atom / target, b'/', false)); }
        out
    }

    fn prune_dead(starts: &[Int], targets: &[Int], program: &Program, limit: Int) -> Outcome<Program> {
        let mut bank = [0; 48]; bank[..starts.len()].copy_from_slice(starts);
        let mut covered: BTreeSet<Int> = starts.iter().copied().collect();
        let required: BTreeSet<Int> = targets.iter().copied().collect();
        let mut first = BTreeSet::new();
        for (position, &[op, a, b, destination]) in program.iter().enumerate() {
            let value = checked(op as u8, bank[a as usize], bank[b as usize], limit)?;
            bank[destination as usize] = value;
            if covered.insert(value) && required.contains(&value) { first.insert(position); }
        }
        let mut live = [false; 48];
        let mut keep = vec![false; program.len()];
        for position in (0..program.len()).rev() {
            let [_, a, b, destination] = program[position];
            if live[destination as usize] || first.contains(&position) {
                keep[position] = true; live[destination as usize] = false;
                live[a as usize] = true; live[b as usize] = true;
            }
        }
        Ok(program.iter().zip(keep).filter_map(|(&instruction, retain)| retain.then_some(instruction)).collect())
    }

    pub fn construct_run() -> Outcome<()> {
        let arguments: Vec<String> = env::args().collect();
        fn argument<T: std::str::FromStr>(args: &[String], index: usize, default: T) -> Outcome<T> {
            args.get(index).map_or(Ok(default), |value| value.parse().map_err(|_| "invalid constructor argument".into()))
        }
        let seed: u64 = argument(&arguments, 1, 0)?;
        let effort: usize = argument(&arguments, 2, 1)?;
        let milliseconds: u64 = argument(&arguments, 3, 2000)?;
        if effort == 0 || effort > 8 || milliseconds == 0 || milliseconds > 600_000 {
            return Err("invalid constructor effort/time budget".into());
        }
        let stdin = io::stdin(); let stdout = io::stdout();
        let mut scanner = Scanner::new(BufReader::new(stdin.lock()));
        let mut writer = BufWriter::new(stdout.lock());
        while let Some(id) = scanner.next()? {
            let limit: Int = scanner.integer()?;
            let ns: usize = scanner.integer()?; let nt: usize = scanner.integer()?;
            if ns == 0 || ns > 48 || nt > 4096 { return Err("invalid constructor dimensions".into()); }
            let mut starts = Vec::new(); let mut targets = Vec::new();
            for _ in 0..ns { starts.push(scanner.integer()?); }
            for _ in 0..nt { targets.push(scanner.integer()?); }
            let machine = Machine::new(limit, starts, targets, seed, effort, milliseconds)?;
            let started = Instant::now();
            let program = machine.solve()?;
            write!(writer, "{} [", id).map_err(|error| error.to_string())?;
            for (index, instruction) in program.iter().enumerate() {
                if index > 0 { write!(writer, ",").map_err(|error| error.to_string())?; }
                write!(writer, "[\"{}\",{},{},{}]", instruction[0] as u8 as char, instruction[1], instruction[2], instruction[3])
                    .map_err(|error| error.to_string())?;
            }
            writeln!(writer, "]").map_err(|error| error.to_string())?;
            writer.flush().map_err(|error| error.to_string())?;
            if env::var_os("SANTA2026_CONSTRUCT_TRACE").is_some() {
                eprintln!("construct id={} seed={} effort={} instructions={} elapsed_ms={}",
                    id, seed, effort, program.len(), started.elapsed().as_millis());
            }
        }
        Ok(())
    }

    #[cfg(test)]
    mod construct_tests {
        use super::*;
        fn machine(starts: Vec<Int>, targets: Vec<Int>, limit: Int) -> Machine {
            Machine::new(limit, starts, targets, 0, 1, 1000).unwrap()
        }
        #[test]
        fn whole_program_shares_a_new_product_between_targets() {
            let program = machine(vec![10], vec![100,200,300], 500).solve().unwrap();
            assert!(program.len() <= 6);
        }
        #[test]
        fn full_seed_bank_can_overwrite_a_seed() {
            let program = machine((1..=48).collect(), vec![49], 100).solve().unwrap();
            assert_eq!(program.len(), 1);
        }
        #[test]
        fn exact_join_operands_stay_live_until_their_last_use() {
            let mut state = machine((2..=49).collect(), vec![10000], 10000);
            let mut plan = Plan::new(&state.bank);
            let a = plan.push(b'*', 47, 48, 10000).unwrap();
            let b = plan.push(b'*', 46, 49, 10000).unwrap();
            plan.push(b'+', a, b, 10000).unwrap();
            state.execute(&plan, 0).unwrap();
            assert!(state.register(4510).is_some());
        }
        #[test]
        fn timed_out_planner_finishes_with_legal_binary_operations() {
            let mut state = machine(vec![3], vec![5,10,15,25], 25);
            state.duration = Duration::ZERO;
            let program = state.solve().unwrap();
            assert!(program.len() < 30);
        }
        #[test]
        fn guarded_products_and_reverse_division_are_exact() {
            assert!(checked(b'*', 1_000_000_000_000_000_000, 2, 1_000_000_000_000_000_000).is_err());
            assert!(checked(b'/', 5, 2, 100).is_err());
            assert!(inverses(3, 12, 100).contains(&(4, b'/', false)));
        }
        #[test]
        fn covered_initial_targets_need_no_instructions() {
            assert!(machine(vec![3,5], vec![3,5], 10).solve().unwrap().is_empty());
        }
        #[test]
        fn bootstrap_squares_pay_one_operation_each() {
            let mut state = machine(vec![7], vec![1_000_000_000], 1_000_000_000);
            state.seed_palette().unwrap();
            assert_eq!(state.program.len(), 7);
            assert!(state.register(65536).is_some());
        }
        #[test]
        fn exact_four_operation_join_reaches_a_value_outside_p2() {
            let state = machine(vec![3,5], vec![99], 1000);
            let dictionary = state.dictionary(true);
            assert!(dictionary.index.get(99) < 0);
            assert!(state.exact(&dictionary, 99, 3).unwrap().is_none());
            let plan = state.exact(&dictionary, 99, 4).unwrap().unwrap();
            assert_eq!(plan.steps.len(), 4);
            let mut after = state.clone(); after.execute(&plan, 0).unwrap();
            assert!(after.register(99).is_some());
        }
        #[test]
        fn residual_anchor_survives_recursive_construction() {
            let mut state = machine(vec![3,5], vec![123457], 1_000_000);
            state.seed_palette().unwrap();
            let dictionary = state.dictionary(true);
            let candidate = state.approximate(&dictionary, 123457).into_iter().next().unwrap();
            let mut plan = Plan::new(&state.bank);
            let a = plan.expression(&dictionary, candidate.a).unwrap();
            if candidate.b >= 0 {
                let b = plan.expression(&dictionary, candidate.b).unwrap();
                plan.push(candidate.op, a, b, state.limit).unwrap();
            }
            let anchor = state.execute(&plan, 0).unwrap();
            let residual = state.binary(candidate.residual, 1u64 << anchor).unwrap();
            let result = if candidate.value < 123457 { state.emit(b'+', anchor, residual, 0) }
                         else { state.emit(b'-', anchor, residual, 0) }.unwrap();
            assert_eq!(state.bank[result], 123457);
        }
        #[test]
        fn maximum_value_fallback_is_positive_and_bounded() {
            let mut state = machine(vec![3], vec![1_000_000_000_000_000_000], 1_000_000_000_000_000_000);
            state.duration = Duration::ZERO;
            assert!(state.solve().unwrap().len() < 120);
        }
        fn approximation_plan(state: &Machine, dictionary: &Dictionary, candidate: &Approximation) -> Plan {
            let mut plan = Plan::new(&state.bank);
            let a = plan.expression(dictionary, candidate.a).unwrap();
            if candidate.b >= 0 {
                let b = plan.expression(dictionary, candidate.b).unwrap();
                plan.push(candidate.op, a, b, state.limit).unwrap();
            }
            plan
        }
        #[test]
        fn zero_residual_p2_diamond_pays_for_four_shared_operations() {
            let mut state = machine(vec![7,11], vec![931], 10000);
            let dictionary = state.dictionary(true);
            // h=7*11; a=h+7; b=h*11; target=a+b. The two P2
            // branches share h, so this is four operations rather than five.
            let candidate = state.approximate(&dictionary, 931).into_iter()
                .find(|candidate| candidate.residual == 0).unwrap();
            assert_eq!(candidate.cost, 4);
            let plan = approximation_plan(&state, &dictionary, &candidate);
            assert_eq!(plan.steps.len(), 4);
            state.execute(&plan, 0).unwrap();
            assert_eq!(state.program.len(), 4);
            assert!(state.register(931).is_some());
            // The full recursive entry must finish directly, not recurse on 0.
            let mut recursive = machine(vec![7,11], vec![931], 10000);
            recursive.solve_target(931, 0, 0, Some(&dictionary)).unwrap();
            assert_eq!(recursive.program.len(), 4);
        }
        #[test]
        fn product_neighbor_above_an_exact_floor_uses_the_cheaper_shared_square() {
            let mut state = machine(vec![1,2,3,5], vec![99], 1000);
            let dictionary = state.dictionary(true);
            // floor(99/10)=9 exists in P1. Checking only the floor and its
            // predecessor misses 10*10. The shared square costs two operations;
            // the competing 4*25 recipe costs three and must be replaced.
            let candidate = state.approximate(&dictionary, 99).into_iter()
                .find(|candidate| candidate.value == 100).unwrap();
            assert_eq!(candidate.cost, 2);
            let plan = approximation_plan(&state, &dictionary, &candidate);
            assert_eq!(plan.steps.len(), 2);
            let anchor = state.execute(&plan, 0).unwrap();
            let unit = state.register(1).unwrap();
            state.emit(b'-', anchor, unit, 0).unwrap();
            assert_eq!(state.program.len(), 3);
            assert!(state.register(99).is_some());
        }
        #[test]
        fn full_seed_changes_recipes_beyond_the_old_period_twelve() {
            let mut first = machine(vec![3,5], vec![99], 1000); first.seed = 12;
            let mut second = first.clone(); second.seed += 3u64 << 60;
            assert_eq!(first.seed % 12, second.seed % 12);
            assert_ne!(first.seed_key(7, 99), second.seed_key(7, 99));
            let targets = vec![44,77,99,143,211];
            let mut programs = BTreeSet::new();
            for seed in [0,12,24,36] {
                let state = Machine::new(1000, vec![3,5], targets.clone(), seed, 1, 1000).unwrap();
                let program = state.clone().solve().unwrap();
                assert_eq!(program, state.solve().unwrap());
                programs.insert(program);
            }
            assert!(programs.len() >= 3);
        }
    }
}

fn main() {
    if let Err(error) = engine::construct_run() {
        eprintln!("Solver error: {}", error); std::process::exit(1);
    }
}
