// SPDX-License-Identifier: Apache-2.0
// Whole-value-graph large-neighborhood search, Rust implementation.
// Search shapes and dominated-helper moves adapted from elfopt.cpp by
// goodpjw2008 (dataset version 49, Apache-2.0); its value-DAG neighborhood
// design credits prvsiyan. Integer replay
// and physical allocation reuse repair.rs, by Tony Li and konbu17.
// See THIRD_PARTY.md and licenses/Apache-2.0.txt.
// CLI: seed milliseconds flags. Bits: 1 helpers, 2 plateau, 4 depth5, 8 kicks,
// 16 atomic multi-output frontier reconstruction (16 alone disables the walk).
// Input/output: the same single-hint token protocol as repair.rs.

#[allow(dead_code)]
mod engine {
    include!("repair.rs");
    use std::collections::HashSet;
    use std::time::{Duration, Instant};

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct Step { op: u8, a: Int, b: Int, value: Int }

    #[derive(Clone, Copy)]
    struct Inverse { value: Int, op: u8, swap: bool }

    fn checked(op: u8, a: Int, b: Int, limit: Int) -> Option<Int> {
        if a <= 0 || b <= 0 { return None; }
        let value = match op {
            b'+' => a.checked_add(b)?,
            b'-' => a.checked_sub(b)?,
            b'*' => a.checked_mul(b)?,
            b'/' if a % b == 0 => a / b,
            _ => return None,
        };
        (value > 0 && value <= limit).then_some(value)
    }

    fn inverses(target: Int, atom: Int, limit: Int) -> Vec<Inverse> {
        let mut out = Vec::with_capacity(6);
        if target > atom { out.push(Inverse { value: target - atom, op: b'+', swap: false }); }
        if target <= limit - atom { out.push(Inverse { value: target + atom, op: b'-', swap: false }); }
        if atom > target { out.push(Inverse { value: atom - target, op: b'-', swap: true }); }
        if atom > 1 && target % atom == 0 { out.push(Inverse { value: target / atom, op: b'*', swap: false }); }
        if atom > 1 && target <= limit / atom { out.push(Inverse { value: target * atom, op: b'/', swap: false }); }
        if atom > target && atom % target == 0 { out.push(Inverse { value: atom / target, op: b'/', swap: true }); }
        out
    }

    fn closing(inv: Inverse, atom: Int, target: Int) -> Step {
        let (a, b) = if inv.swap { (atom, inv.value) } else { (inv.value, atom) };
        Step { op: inv.op, a, b, value: target }
    }

    fn square_root(value: Int) -> Int {
        let mut root = (value as f64).sqrt() as Int;
        while i128::from(root) * i128::from(root) > i128::from(value) { root -= 1; }
        while i128::from(root + 1) * i128::from(root + 1) <= i128::from(value) { root += 1; }
        root
    }

    #[derive(Default)]
    struct Metrics {
        attempts: usize, helper_attempts: usize, accepted: usize, plateaus: usize,
        kicks: usize, improvements: usize, catalogs: usize, max_pool: usize,
        max_s1: usize, need_aborts: usize, deadline_aborts: usize, skipped_identity: usize,
        frontier_cuts: usize, frontier_orders: usize, frontier_ports: usize, frontier_wins: usize,
        target_blocks: usize, target_block_orders: usize, target_block_wins: usize,
    }

    struct Searcher {
        limit: Int, pool: Vec<Int>, known: HashSet<Int>,
        one: HashMap<Int, Step>, one_values: Vec<Int>, sorted: Vec<Int>,
        deadline: Instant, ticks: usize, minimum: usize, root: Int,
        identity: Vec<(Int,u8,Int,Int)>, skipped_identity: usize,
    }

    impl Searcher {
        fn expired(&mut self) -> bool {
            self.ticks += 1;
            self.ticks & 255 == 0 && Instant::now() >= self.deadline
        }

        fn new(pool: Vec<Int>, limit: Int, deadline: Instant) -> Option<Self> {
            let known: HashSet<Int> = pool.iter().copied().collect();
            let mut search = Self { limit, pool, known, one: HashMap::new(),
                one_values: Vec::new(), sorted: Vec::new(), deadline, ticks: 0, minimum: 0, root: 0,
                identity: Vec::new(), skipped_identity: 0 };
            for i in 0..search.pool.len() {
                for j in 0..=i {
                    if search.expired() { return None; }
                    let a = search.pool[i]; let b = search.pool[j];
                    for (op, x, y) in [(b'+', a, b), (b'*', a, b),
                        (b'-', a.max(b), a.min(b)), (b'/', a.max(b), a.min(b))] {
                        if let Some(value) = checked(op, x, y, limit) {
                            if !search.known.contains(&value) && !search.one.contains_key(&value) {
                                search.one.insert(value, Step { op, a: x, b: y, value });
                                search.one_values.push(value);
                            }
                        }
                    }
                }
            }
            search.sorted = search.pool.iter().chain(&search.one_values).copied().collect();
            search.sorted.sort_unstable(); search.sorted.dedup();
            Some(search)
        }

        fn c01(&self, value: Int) -> Option<Vec<Step>> {
            if self.known.contains(&value) { Some(Vec::new()) }
            else { self.one.get(&value).copied().map(|step| vec![step]) }
        }

        // Numeric identities can share a newly computed value. Charge distinct
        // actual operations, and require operands to have been materialized.
        fn normalize(&self, steps: Vec<Step>, target: Int, max: usize) -> Option<Vec<Step>> {
            let mut built = self.known.clone(); let mut out = Vec::new();
            for step in steps {
                if built.contains(&step.value) { continue; }
                if !built.contains(&step.a) || !built.contains(&step.b)
                    || checked(step.op, step.a, step.b, self.limit) != Some(step.value) { return None; }
                built.insert(step.value); out.push(step);
                if out.len() > max { return None; }
            }
            built.contains(&target).then_some(out)
        }

        fn finish(&mut self, steps: Vec<Step>, target: Int, max: usize) -> Option<Vec<Step>> {
            let out=self.normalize(steps,target,max)?;
            if target==self.root && !self.identity.is_empty() {
                let mut key:Vec<_>=out.iter().map(|step| {
                    let (a,b)=if (step.op==b'+' || step.op==b'*') && step.a>step.b {
                        (step.b,step.a)
                    } else { (step.a,step.b) };
                    (step.value,step.op,a,b)
                }).collect();
                key.sort_unstable();
                if key==self.identity { self.skipped_identity+=1; return None; }
            }
            Some(out)
        }

        fn cost2(&mut self, target: Int) -> Option<Vec<Step>> {
            if let Some(out) = self.c01(target) {
                if let Some(out)=self.finish(out,target,2) { return Some(out); }
            }
            for index in 0..self.pool.len() {
                if self.expired() { return None; }
                let atom = self.pool[index];
                for inv in inverses(target, atom, self.limit) {
                    if let Some(mut out) = self.c01(inv.value) {
                        out.push(closing(inv, atom, target));
                        if let Some(out) = self.finish(out, target, 2) { return Some(out); }
                    }
                }
            }
            for (op, value) in [(b'+', if target % 2 == 0 { target / 2 } else { 0 }),
                (b'*', square_root(target))] {
                if value > 0 && checked(op, value, value, self.limit) == Some(target) {
                    if let Some(mut out) = self.c01(value) {
                        out.push(Step { op, a: value, b: value, value: target });
                        if let Some(out)=self.finish(out,target,2) { return Some(out); }
                    }
                }
            }
            None
        }

        fn find(&mut self, target: Int, max: usize, depth5: bool, metrics: &mut Metrics) -> Option<Vec<Step>> {
            if let Some(out) = self.c01(target) {
                if let Some(out)=self.finish(out,target,max) {
                    if out.len()>=self.minimum { return Some(out); }
                }
            }
            // A one-op catalog stores one representative recipe per value.
            // If it is the old root recipe, test the other direct operand pairs.
            if target==self.root && !self.identity.is_empty() {
                for index in 0..self.pool.len() {
                    if self.expired() { return None; }
                    let atom=self.pool[index];
                    for inv in inverses(target,atom,self.limit) {
                        if self.known.contains(&inv.value) {
                            if let Some(out)=self.finish(vec![closing(inv,atom,target)],target,max) {
                                if out.len()>=self.minimum { return Some(out); }
                            }
                        }
                    }
                }
            }
            if max < 2 { return None; }
            if let Some(out) = self.cost2(target) { if out.len() >= self.minimum { return Some(out); } }
            if Instant::now() >= self.deadline || max < 3 { return None; }
            // Balanced three-op trees: S1 op S1.
            for index in 0..self.one_values.len() {
                if self.expired() { return None; }
                let atom = self.one_values[index];
                for inv in inverses(target, atom, self.limit) {
                    if let Some(other) = self.one.get(&inv.value) {
                        let out = vec![self.one[&atom], *other, closing(inv, atom, target)];
                        if let Some(out) = self.finish(out, target, 3) { if out.len() >= self.minimum { return Some(out); } }
                    }
                }
            }
            // Chain trees: (S1 op P) op P, including divisions whose numerator
            // is larger than the target; no monotonicity assumption is used.
            for index in 0..self.pool.len() {
                if self.expired() { return None; }
                let atom = self.pool[index];
                for inv in inverses(target, atom, self.limit) {
                    if let Some(mut out) = self.cost2(inv.value) {
                        out.push(closing(inv, atom, target));
                        if let Some(out) = self.finish(out, target, 3) { if out.len() >= self.minimum { return Some(out); } }
                    }
                }
            }
            if Instant::now() >= self.deadline || max < 4 { return None; }
            if let Some(out) = self.find4(target, metrics) { return Some(out); }
            if Instant::now() >= self.deadline || max < 5 || !depth5 { return None; }
            self.near_miss(target, max)
        }

        fn find4(&mut self, target: Int, metrics: &mut Metrics) -> Option<Vec<Step>> {
            // Explicit bounded memory: abort this shape at 750k distinct needs
            // (~100 MB with standard HashMap/Vec overhead), never truncate P.
            const MAX_NEEDS: usize = 750_000;
            let mut need: HashMap<Int, [Step; 2]> = HashMap::new();
            for index in 0..self.one_values.len() {
                if self.expired() { return None; }
                let atom = self.one_values[index];
                for inv in inverses(target, atom, self.limit) {
                    if !self.known.contains(&inv.value) && !self.one.contains_key(&inv.value) {
                        need.entry(inv.value).or_insert([self.one[&atom], closing(inv, atom, target)]);
                    }
                }
                if need.len() > MAX_NEEDS { metrics.need_aborts += 1; return None; }
            }
            for index in 0..self.pool.len() {
                let atom = self.pool[index];
                for inv in inverses(target, atom, self.limit) {
                    for second in 0..self.pool.len() {
                        if self.expired() { return None; }
                        let other = self.pool[second];
                        for inner in inverses(inv.value, other, self.limit) {
                            if !self.known.contains(&inner.value) && !self.one.contains_key(&inner.value) {
                                need.entry(inner.value).or_insert([closing(inner, other, inv.value), closing(inv, atom, target)]);
                            }
                        }
                    }
                    if need.len() > MAX_NEEDS { metrics.need_aborts += 1; return None; }
                }
            }
            for index in 0..self.one_values.len() {
                let value = self.one_values[index];
                for second in 0..self.pool.len() {
                    if self.expired() { return None; }
                    let atom = self.pool[second];
                    for (op, a, b) in [(b'+',value,atom),(b'*',value,atom),
                        (b'-',value,atom),(b'-',atom,value),(b'/',value,atom),(b'/',atom,value)] {
                        if let Some(result) = checked(op, a, b, self.limit) {
                            if let Some(tail) = need.get(&result) {
                                let out = vec![self.one[&value], Step { op, a, b, value: result }, tail[0], tail[1]];
                                if let Some(out) = self.finish(out, target, 4) { if out.len() >= self.minimum { return Some(out); } }
                            }
                        }
                    }
                }
            }
            None
        }

        fn consider_product(&mut self, target: Int, x: Int, y: Int,
                            xsteps: &[Step], ysteps: &[Step], max: usize) -> Option<Vec<Step>> {
            let product = checked(b'*', x, y, self.limit)?;
            let mut steps = xsteps.to_vec(); steps.extend_from_slice(ysteps);
            steps.push(Step { op: b'*', a: x, b: y, value: product });
            if product == target { return self.finish(steps, target, max); }
            let residual = (product - target).abs();
            let distinct_base:HashSet<Int>=steps.iter().map(|step|step.value)
                .filter(|value|!self.known.contains(value)).collect();
            let residual_steps = self.c01(residual).or_else(|| {
                // Reusing the same derived X and Y pays its definition once.
                if distinct_base.len()+3<=max { self.cost2(residual) } else { None }
            })?;
            steps.extend(residual_steps);
            steps.push(if product > target { Step { op: b'-', a: product, b: residual, value: target } }
                else { Step { op: b'+', a: product, b: residual, value: target } });
            self.finish(steps, target, max)
        }

        fn near_miss(&mut self, target: Int, max: usize) -> Option<Vec<Step>> {
            for index in 0..self.sorted.len() {
                if self.expired() { return None; }
                let x = self.sorted[index]; if x < 2 { continue; }
                let quotient = target / x; if quotient < 1 { break; }
                let xsteps = self.c01(x)?;
                let center = self.sorted.partition_point(|&value| value < quotient);
                for offset in -2isize..=1 {
                    let yi = center as isize + offset;
                    if yi < 0 || yi as usize >= self.sorted.len() { continue; }
                    let y = self.sorted[yi as usize];
                    let ysteps = self.c01(y)?;
                    if let Some(out) = self.consider_product(target, x, y, &xsteps, &ysteps, max) { if out.len() >= self.minimum { return Some(out); } }
                }
                if xsteps.is_empty() {
                    for y in [quotient, quotient + 1] {
                        if y < 2 || self.c01(y).is_some() { continue; }
                        if let Some(ysteps) = self.cost2(y) {
                            if let Some(out) = self.consider_product(target, x, y, &xsteps, &ysteps, max) { if out.len() >= self.minimum { return Some(out); } }
                        }
                    }
                }
            }
            None
        }
    }

    fn active_values(puzzle: &Puzzle) -> Outcome<BTreeSet<Int>> {
        let seeds: BTreeSet<Int> = puzzle.starts.iter().copied().collect();
        let mut colors: BTreeMap<Int,u8> = seeds.iter().map(|&v| (v,2)).collect();
        for &target in &puzzle.targets {
            let mut stack = vec![(target,false)];
            while let Some((value, leaving)) = stack.pop() {
                if seeds.contains(&value) { continue; }
                if leaving { colors.insert(value,2); continue; }
                match colors.get(&value).copied().unwrap_or(0) {
                    2 => continue, 1 => return Err("cyclic LNS value graph".into()), _ => (),
                }
                let &(a,b) = puzzle.arguments.get(&value).ok_or("missing LNS recipe")?;
                let expression = puzzle.definitions.get(&value).ok_or("missing LNS expression")?;
                if checked(expression.op,a,b,puzzle.limit) != Some(value) { return Err("invalid LNS arithmetic".into()); }
                colors.insert(value,1); stack.push((value,true)); stack.push((b,false)); stack.push((a,false));
            }
        }
        Ok(colors.into_keys().collect())
    }

    fn prune(puzzle: &mut Puzzle) -> Outcome<()> {
        let active = active_values(puzzle)?;
        puzzle.arguments.retain(|value,_| active.contains(value));
        puzzle.definitions.retain(|value,_| active.contains(value));
        puzzle.order.retain(|value| active.contains(value));
        Ok(())
    }

    fn graph_key(puzzle: &Puzzle) -> Vec<(Int,u8,Int,Int)> {
        puzzle.arguments.iter().map(|(&v,&(a,b))| (v,puzzle.definitions[&v].op,a,b)).collect()
    }

    // Recompute reachability after replacing root with a boundary value. Every
    // removed ancestor reaches all live targets solely through this root.
    fn exclusive_cone(puzzle: &Puzzle, root: Int) -> Outcome<BTreeSet<Int>> {
        let active = active_values(puzzle)?;
        let seeds: BTreeSet<Int> = puzzle.starts.iter().copied().collect();
        let mut retained = seeds.clone(); let mut stack = puzzle.targets.clone();
        while let Some(value) = stack.pop() {
            if value == root || !retained.insert(value) { continue; }
            if let Some(&(a,b)) = puzzle.arguments.get(&value) { stack.push(a); stack.push(b); }
        }
        Ok(active.difference(&retained).filter(|v| !seeds.contains(v)).copied().collect())
    }

    fn legal_pool(puzzle: &Puzzle, root: Int, removed: &BTreeSet<Int>) -> Vec<Int> {
        let blocked = puzzle.descendants(root);
        puzzle.definitions.keys().filter(|v| !removed.contains(v) && !blocked.contains(v)).copied().collect()
    }

    // An interface cuts every direct consumer of a shared value at once.
    // The old shared value can disappear only after ALL of its consumers have
    // legal replacement recipes. This counts the whole paid dependency union.
    fn frontier_context(puzzle: &Puzzle, roots: &[Int]) -> Outcome<(BTreeSet<Int>, Vec<Int>)> {
        let active=active_values(puzzle)?;
        let boundary:BTreeSet<Int>=roots.iter().copied().collect();
        let mut retained:BTreeSet<Int>=puzzle.starts.iter().copied().collect();
        let mut stack=puzzle.targets.clone();
        while let Some(value)=stack.pop() {
            if boundary.contains(&value) || !retained.insert(value) { continue; }
            if let Some(&(a,b))=puzzle.arguments.get(&value) { stack.push(a); stack.push(b); }
        }
        let removed:BTreeSet<Int>=active.difference(&retained).copied().collect();
        let blocked:BTreeSet<Int>=roots.iter().flat_map(|&root|puzzle.descendants(root)).collect();
        let pool=retained.difference(&blocked).copied().collect();
        Ok((removed,pool))
    }

    fn rebuild_frontier(puzzle: &Puzzle, roots: &[Int], removed: &BTreeSet<Int>, pool: &[Int],
                        deadline: Instant, generator: &mut Mt19937_64, metrics: &mut Metrics, depth: usize,
                        filter_identity: bool)
                        -> Option<(Puzzle,Program)> {
        let mut pending=roots.to_vec(); generator.shuffle(&mut pending);
        let mut available=pool.to_vec(); generator.shuffle(&mut available);
        let mut built:HashSet<Int>=available.iter().copied().collect();
        let mut steps=Vec::new();
        // Search a coordinated construction, exposing every newly paid value
        // to the remaining ports. No old removed parent is a free leaf.
        while let Some(target)=pending.pop() {
            if Instant::now()>=deadline { return None; }
            if built.contains(&target) { continue; }
            let budget=removed.len().checked_sub(steps.len()+1)?;
            if budget==0 { return None; }
            let mut search=Searcher::new(available.clone(),puzzle.limit,deadline)?;
            if filter_identity {
                // Only reject the old root derivation down to this exact paid
                // prefix. Identity filtering must not affect residual searches.
                search.root=target;
                let mut needed=BTreeSet::new(); let mut pending_old=vec![target];
                while let Some(value)=pending_old.pop() {
                    if built.contains(&value) || !needed.insert(value) { continue; }
                    let &(mut a,mut b)=puzzle.arguments.get(&value)?;
                    let op=puzzle.definitions.get(&value)?.op;
                    pending_old.push(a); pending_old.push(b);
                    if (op==b'+' || op==b'*') && a>b { std::mem::swap(&mut a,&mut b); }
                    search.identity.push((value,op,a,b));
                }
                search.identity.sort_unstable();
            }
            metrics.catalogs+=1;
            metrics.max_pool=metrics.max_pool.max(search.pool.len());
            metrics.max_s1=metrics.max_s1.max(search.one.len());
            let result=search.find(target,budget.min(depth),depth>=5,metrics);
            metrics.skipped_identity+=search.skipped_identity;
            let plan=result?;
            for step in plan {
                if built.insert(step.value) { available.push(step.value); steps.push(step); }
            }
        }
        if steps.len()>=removed.len() || !roots.iter().all(|root|built.contains(root)) { return None; }
        let trial=rebuild(puzzle,removed,*roots.last()?,&steps).ok()?;
        let program=verified_program(&trial).ok()?;
        Some((trial,program))
    }

    const MAX_TARGET_BLOCKS: usize=32;

    fn sampled_target_blocks(puzzle:&Puzzle,generator:&mut Mt19937_64,maximum:usize)->Vec<Vec<Int>> {
        if maximum==0 { return Vec::new(); }
        let positions:BTreeMap<Int,usize>=puzzle.order.iter().enumerate().map(|(i,&v)|(v,i)).collect();
        let mut completion:Vec<Int>=puzzle.targets.iter().copied()
            .filter(|v|puzzle.arguments.contains_key(v)).collect::<BTreeSet<_>>().into_iter().collect();
        completion.sort_by_key(|v|(positions.get(v).copied().unwrap_or(usize::MAX),*v));
        let mut shuffled=completion.clone(); generator.shuffle(&mut shuffled);
        let mut blocks=Vec::new(); let mut seen=BTreeSet::new();
        for order in [&completion,&shuffled] {
            for length in [3,4,6,8] {
                if order.len()<length { continue; }
                let mut starts:Vec<usize>=(0..=order.len()-length).collect(); generator.shuffle(&mut starts);
                for start in starts.into_iter().take(4) {
                    if blocks.len()>=maximum { return blocks; }
                    let block=order[start..start+length].to_vec();
                    let mut key=block.clone(); key.sort_unstable();
                    if seen.insert(key) { blocks.push(block); }
                }
            }
        }
        blocks
    }

    type FrontierCandidate=(usize,Int,Vec<Int>,BTreeSet<Int>,Vec<Int>);

    fn try_frontiers(walk:&mut Walk,mut candidates:Vec<FrontierCandidate>,deadline:Instant,
                     generator:&mut Mt19937_64,metrics:&mut Metrics,depth:usize,target_blocks:bool)
                     ->Outcome<bool> {
        generator.shuffle(&mut candidates);
        candidates.sort_by_key(|candidate|std::cmp::Reverse(candidate.0));
        for (_,_,ports,removed,pool) in candidates {
            if Instant::now()>=deadline { break; }
            metrics.frontier_cuts+=1; metrics.frontier_ports=metrics.frontier_ports.max(ports.len());
            for variant in 0..4 {
                if Instant::now()>=deadline { break; }
                metrics.frontier_orders+=1;
                if target_blocks { metrics.target_block_orders+=1; }
                if let Some((trial,program))=rebuild_frontier(&walk.current,&ports,&removed,&pool,
                    deadline,generator,metrics,depth,variant%2==1) {
                    if walk.accept(trial,program,false,false,metrics) {
                        metrics.frontier_wins+=1;
                        if target_blocks { metrics.target_block_wins+=1; }
                        return Ok(true);
                    }
                }
            }
        }
        Ok(false)
    }

    fn frontier_pass(walk: &mut Walk, deadline: Instant, generator: &mut Mt19937_64,
                     metrics: &mut Metrics, depth: usize) -> Outcome<bool> {
        let mut users:BTreeMap<Int,BTreeSet<Int>>=BTreeMap::new();
        for (&value,&(a,b)) in &walk.current.arguments {
            users.entry(a).or_default().insert(value); users.entry(b).or_default().insert(value);
        }
        let targets:BTreeSet<Int>=walk.current.targets.iter().copied().collect();
        let mut candidates=Vec::new();
        for (&pivot,consumers) in &users {
            if Instant::now()>=deadline { break; }
            // Fixed, explicit interface-size bound; no subset may pretend it
            // retires a scaffold still used by an omitted consumer.
            if targets.contains(&pivot) || !walk.current.arguments.contains_key(&pivot)
                || !(2..=12).contains(&consumers.len()) { continue; }
            let ports:Vec<Int>=consumers.iter().copied().collect();
            let (removed,pool)=frontier_context(&walk.current,&ports)?;
            if !removed.contains(&pivot) || removed.len()<=ports.len() { continue; }
            candidates.push((removed.len()-ports.len(),pivot,ports,removed,pool));
        }
        let remaining=MAX_TARGET_BLOCKS.saturating_sub(metrics.target_blocks);
        let blocks=sampled_target_blocks(&walk.current,generator,remaining);
        metrics.target_blocks+=blocks.len();
        let mut block_candidates=Vec::new();
        for ports in blocks {
            if Instant::now()>=deadline { break; }
            let (removed,pool)=frontier_context(&walk.current,&ports)?;
            if removed.len()>ports.len() {
                block_candidates.push((removed.len()-ports.len(),0,ports,removed,pool));
            }
        }
        // Reserve a separate half-budget for actual-target groups so a large
        // helper cut cannot consume every attempt before a block is examined.
        let now=Instant::now();
        let block_deadline=if candidates.is_empty() { deadline }
            else { now+deadline.saturating_duration_since(now)/2 };
        if try_frontiers(walk,block_candidates,block_deadline,generator,metrics,depth.min(3),true)? {
            return Ok(true);
        }
        try_frontiers(walk,candidates,deadline,generator,metrics,depth,false)
    }

    fn rebuild(puzzle: &Puzzle, removed: &BTreeSet<Int>, root: Int, steps: &[Step]) -> Outcome<Puzzle> {
        let mut trial = puzzle.clone();
        for value in removed { trial.arguments.remove(value); trial.definitions.remove(value); }
        let insertion = puzzle.order.iter().take_while(|&&v| v != root)
            .filter(|v| !removed.contains(v)).count();
        trial.order.retain(|v| !removed.contains(v));
        let mut inserted = Vec::new();
        for &step in steps {
            // Existing consumers of the root are retained, but the replacement
            // cannot use one as a leaf (legal_pool excludes every descendant).
            if !trial.definitions.contains_key(&step.a) || !trial.definitions.contains_key(&step.b) {
                return Err("LNS construction reads an unavailable value".into());
            }
            if checked(step.op,step.a,step.b,trial.limit) != Some(step.value) { return Err("invalid LNS replacement".into()); }
            trial.arguments.insert(step.value,(step.a,step.b));
            trial.definitions.insert(step.value,Expr { value:step.value, a:-1,b:-1,op:step.op,cost:0 });
            inserted.push(step.value);
        }
        let inserted_set: BTreeSet<Int> = inserted.iter().copied().collect();
        let before = trial.order[..insertion.min(trial.order.len())].iter()
            .filter(|v| !inserted_set.contains(v)).count();
        trial.order.retain(|v| !inserted_set.contains(v));
        trial.order.splice(before..before, inserted);
        if !trial.definitions.contains_key(&root) { return Err("LNS replacement missed root".into()); }
        prune(&mut trial)?; Ok(trial)
    }

    fn verified_program(puzzle: &Puzzle) -> Outcome<Program> {
        let active = active_values(puzzle)?;
        let seeds: BTreeSet<Int> = puzzle.starts.iter().copied().collect();
        let positions: BTreeMap<Int,usize> = puzzle.order.iter().enumerate()
            .map(|(index,&value)| (value,index)).collect();
        let mut indegree = BTreeMap::new();
        let mut watchers: BTreeMap<Int,Vec<Int>> = BTreeMap::new();
        for &value in active.difference(&seeds) {
            let &(a,b) = puzzle.arguments.get(&value).ok_or("missing scheduled LNS recipe")?;
            let mut count = 0;
            for operand in BTreeSet::from([a,b]) {
                if !seeds.contains(&operand) { count+=1; watchers.entry(operand).or_default().push(value); }
            }
            indegree.insert(value,count);
        }
        let mut ready = BTreeSet::new();
        for (&value,&count) in &indegree {
            if count==0 { ready.insert((positions.get(&value).copied().unwrap_or(usize::MAX),value)); }
        }
        let mut sequence = Vec::new();
        while let Some((_,value)) = ready.pop_first() {
            sequence.push(value);
            for &user in watchers.get(&value).into_iter().flatten() {
                let count=indegree.get_mut(&user).ok_or("missing LNS schedule user")?;
                *count-=1;
                if *count==0 { ready.insert((positions.get(&user).copied().unwrap_or(usize::MAX),user)); }
            }
        }
        if sequence.len()!=indegree.len() { return Err("cyclic LNS schedule".into()); }
        let mut uses:BTreeMap<Int,usize>=BTreeMap::new();
        for &value in &sequence {
            let (a,b)=puzzle.arguments[&value];
            for operand in BTreeSet::from([a,b]) { *uses.entry(operand).or_default()+=1; }
        }
        let mut bank=[0;48]; bank[..puzzle.starts.len()].copy_from_slice(&puzzle.starts);
        let mut program=Vec::new();
        for value in sequence {
            let (a,b)=puzzle.arguments[&value];
            let ra=bank.iter().position(|&v|v==a).ok_or("LNS schedule requires rematerialization")?;
            let rb=bank.iter().position(|&v|v==b).ok_or("LNS schedule requires rematerialization")?;
            for operand in BTreeSet::from([a,b]) { *uses.get_mut(&operand).ok_or("missing LNS operand use")?-=1; }
            // Targets count when observed, so dead targets and dead starts can
            // be recycled immediately; source reads precede the destination.
            let rd=bank.iter().position(|v|*v==0 || uses.get(v).copied().unwrap_or(0)==0)
                .ok_or("LNS register pressure exceeds 48")?;
            let op=puzzle.definitions[&value].op;
            program.push([op as i32,ra as i32,rb as i32,rd as i32]); bank[rd]=value;
        }
        if program.len()>4096 { return Err("LNS program too long".into()); }
        let mut replay=puzzle.clone(); replay.load_hint(&program)?;
        Ok(program)
    }

    type RecipeKey = (Int,u8,Int,Int);
    struct ArchiveEntry { program:Program, recipes:BTreeSet<RecipeKey> }
    #[derive(Default)]
    struct Archive { entries:Vec<ArchiveEntry> }
    impl Archive {
        fn prune(&mut self,best:usize) { self.entries.retain(|entry|entry.program.len()<=best+2); }
        fn consider(&mut self,puzzle:&Puzzle,program:&Program,best:usize) {
            self.prune(best);
            if program.len()>best+2 { return; }
            let recipes:BTreeSet<_>=graph_key(puzzle).into_iter().map(|(value,op,a,b)| {
                if (op==b'+' || op==b'*') && a>b { (value,op,b,a) } else { (value,op,a,b) }
            }).collect();
            if self.entries.iter().any(|entry|entry.recipes==recipes) { return; }
            self.entries.push(ArchiveEntry{program:program.clone(),recipes});
            if self.entries.len()>8 {
                // Drop the graph contributing the fewest recipes absent from
                // the other retained graphs. Executed cost breaks coverage ties.
                let remove=(0..self.entries.len()).min_by_key(|&index| {
                    let others:BTreeSet<_>=self.entries.iter().enumerate().filter(|(i,_)|*i!=index)
                        .flat_map(|(_,entry)|entry.recipes.iter().copied()).collect();
                    let novel=self.entries[index].recipes.difference(&others).count();
                    (novel,std::cmp::Reverse(self.entries[index].program.len()),index)
                }).unwrap();
                self.entries.remove(remove);
            }
        }
        fn programs(mut self,best:usize)->Vec<Program> {
            self.prune(best); self.entries.into_iter().map(|entry|entry.program).collect()
        }
    }

    struct Walk {
        current: Puzzle, current_cost: usize, best: Program, best_graph: Puzzle,
        seen: HashSet<Vec<(Int,u8,Int,Int)>>, archive:Option<Archive>,
    }

    impl Walk {
        fn accept(&mut self, trial: Puzzle, program: Program, plateau: bool, kick: bool, metrics: &mut Metrics) -> bool {
            let cost = program.len();
            let limit = if kick { self.best.len().saturating_add(2) } else { self.current_cost };
            if cost > limit || (!kick && !plateau && cost == self.current_cost) { return false; }
            if !self.seen.insert(graph_key(&trial)) { return false; }
            if cost < self.best.len() {
                self.best = program.clone(); self.best_graph = trial.clone(); metrics.improvements += 1;
            }
            if let Some(archive)=&mut self.archive { archive.consider(&trial,&program,self.best.len()); }
            if cost == self.current_cost { metrics.plateaus += 1; }
            if cost > self.current_cost { metrics.kicks += 1; }
            self.current = trial; self.current_cost = cost; metrics.accepted += 1; true
        }
    }

    fn optimize_capture(mut puzzle: Puzzle, hint: &Program, seed: u64, milliseconds: u64, flags: u8,
                        capture:bool) -> Outcome<(Program,Metrics,Vec<Program>)> {
        puzzle.load_hint(hint)?; prune(&mut puzzle)?;
        let deadline = Instant::now() + Duration::from_millis(milliseconds);
        let mut metrics = Metrics::default();
        let mut best = hint.clone();
        if let Ok(program) = verified_program(&puzzle) { if program.len() < best.len() { best=program; } }
        let mut walk = Walk { current:puzzle.clone(), current_cost:best.len(), best, best_graph:puzzle,
            seen:HashSet::new(), archive:capture.then(Archive::default) };
        walk.seen.insert(graph_key(&walk.current));
        let mut generator = Mt19937_64::new(seed); let mut rounds = 0usize;
        let walk_enabled=flags&16==0 || flags&(1|2|8)!=0;
        if flags&16!=0 {
            let frontier_deadline=if !walk_enabled { deadline }
                else { (Instant::now()+Duration::from_millis(milliseconds/3)).min(deadline) };
            while Instant::now()<frontier_deadline {
                if !frontier_pass(&mut walk,frontier_deadline,&mut generator,&mut metrics,if flags&4!=0 {5}else{3})? { break; }
            }
        }
        while walk_enabled && Instant::now()<deadline {
            let mut roots = walk.current.targets.clone();
            if flags & 1 != 0 { roots.extend(walk.current.arguments.keys().copied()); }
            roots.sort_unstable(); roots.dedup();
            generator.shuffle(&mut roots);
            // Favor cones that admit a lower-cost replacement, preserving random
            // ties so repeated runs explore different equal-cost expressions.
            let mut ranked = Vec::new();
            for root in roots {
                if Instant::now() >= deadline { break; }
                if !walk.current.arguments.contains_key(&root) { continue; }
                let removed = exclusive_cone(&walk.current,root)?;
                if removed.len()>=2 || (removed.len()==1 && flags&(2|8)!=0) { ranked.push((root,removed)); }
            }
            ranked.sort_by_key(|(_,cone)| std::cmp::Reverse(cone.len()));
            let mut moved = false;
            // A kick is considered only after a stalled round. All acceptance
            // budgets are based on executed instructions, not distinct nodes.
            let kick = flags & 8 != 0 && rounds % 3 == 2;
            for (root,removed) in ranked {
                if Instant::now() >= deadline { break; }
                metrics.attempts += 1;
                if !walk.current.targets.contains(&root) { metrics.helper_attempts += 1; }
                let plateau = flags & 2 != 0;
                let allowance = if kick { 2 } else if plateau { 0 } else { -1 };
                let maximum = ((removed.len() as isize + allowance).max(0) as usize)
                    .min(if flags & 4 != 0 { 5 } else { 4 });
                let minimum = if kick { removed.len()+1 } else { 0 };
                if maximum == 0 || minimum > maximum { continue; }
                let mut pool = legal_pool(&walk.current,root,&removed);
                generator.shuffle(&mut pool);
                let Some(mut search) = Searcher::new(pool,walk.current.limit,deadline) else { metrics.deadline_aborts+=1; break; };
                search.minimum=minimum; search.root=root;
                search.identity=removed.iter().filter_map(|&value| {
                    let &(mut a,mut b)=walk.current.arguments.get(&value)?;
                    let op=walk.current.definitions[&value].op;
                    if (op==b'+' || op==b'*') && a>b { std::mem::swap(&mut a,&mut b); }
                    Some((value,op,a,b))
                }).collect();
                metrics.catalogs += 1; metrics.max_pool=metrics.max_pool.max(search.pool.len());
                metrics.max_s1=metrics.max_s1.max(search.one.len());
                let result=search.find(root,maximum,flags&4!=0,&mut metrics);
                metrics.skipped_identity+=search.skipped_identity;
                let Some(steps)=result else {
                    if Instant::now() >= deadline { metrics.deadline_aborts+=1; break; }
                    continue;
                };
                if Instant::now() >= deadline { break; }
                let Ok(trial) = rebuild(&walk.current,&removed,root,&steps) else { continue; };
                let Ok(program) = verified_program(&trial) else { continue; };
                if walk.accept(trial,program,plateau,kick,&mut metrics) { moved=true; break; }
            }
            rounds += 1;
            if !moved && flags & (2|8) == 0 { break; }
            if rounds % 8 == 0 { walk.current=walk.best_graph.clone(); walk.current_cost=walk.best.len(); }
            if ranked_empty_or_expired(&walk.current,deadline) { break; }
        }
        let archive=walk.archive.map(|archive|archive.programs(walk.best.len())).unwrap_or_default();
        Ok((walk.best,metrics,archive))
    }

    fn optimize(puzzle:Puzzle,hint:&Program,seed:u64,milliseconds:u64,flags:u8)->Outcome<(Program,Metrics)> {
        let (program,metrics,_)=optimize_capture(puzzle,hint,seed,milliseconds,flags,false)?;
        Ok((program,metrics))
    }

    fn ranked_empty_or_expired(puzzle: &Puzzle, deadline: Instant) -> bool {
        puzzle.arguments.is_empty() || Instant::now() >= deadline
    }

    fn json_string(text:&str)->String {
        let mut out=String::from("\"");
        for ch in text.chars() {
            match ch {
                '"'=>out.push_str("\\\""), '\\'=>out.push_str("\\\\"),
                '\n'=>out.push_str("\\n"), '\r'=>out.push_str("\\r"), '\t'=>out.push_str("\\t"),
                ch if ch<'\u{20}'=>out.push_str(&format!("\\u{:04x}",ch as u32)),
                ch=>out.push(ch),
            }
        }
        out.push('"');out
    }

    fn program_json(program:&Program)->String {
        let mut out=String::from("[");
        for (index,step) in program.iter().enumerate() {
            if index>0 { out.push(','); }
            out.push_str(&format!("[\"{}\",{},{},{}]",step[0] as u8 as char,step[1],step[2],step[3]));
        }
        out.push(']');out
    }

    fn write_archive(path:&std::path::Path,id:&str,programs:&[Program])->Outcome<()> {
        let mut json=format!("{{\"version\":1,\"id\":{},\"programs\":[",json_string(id));
        for (index,program) in programs.iter().enumerate() {
            if index>0 {json.push(',');} json.push_str(&program_json(program));
        }
        json.push_str("]}");
        let mut temporary=path.as_os_str().to_os_string();temporary.push(".tmp");
        std::fs::write(&temporary,json.as_bytes()).map_err(|e|e.to_string())?;
        std::fs::rename(&temporary,path).map_err(|e|e.to_string())?;Ok(())
    }

    pub fn run_lns() -> Outcome<()> {
        let args:Vec<String>=env::args().collect();
        let seed:u64=args.get(1).map_or(Ok(0),|s|s.parse().map_err(|_|"invalid seed"))?;
        let milliseconds:u64=args.get(2).map_or(Ok(2000),|s|s.parse().map_err(|_|"invalid milliseconds"))?;
        let flags:u8=args.get(3).map_or(Ok(15),|s|s.parse().map_err(|_|"invalid flags"))?;
        if milliseconds==0 || milliseconds>600_000 || flags>31 { return Err("invalid LNS budget".into()); }
        let stdin=io::stdin(); let mut scanner=Scanner::new(BufReader::new(stdin.lock()));
        let stdout=io::stdout(); let mut writer=BufWriter::new(stdout.lock());
        while let Some(id)=scanner.next()? {
            let limit:Int=scanner.integer()?; let ns:usize=scanner.integer()?; let nt:usize=scanner.integer()?;
            if !(1..=1_000_000_000_000_000_000).contains(&limit) || ns==0 || ns>48 || nt>4096 { return Err("invalid LNS dimensions".into()); }
            let mut starts=Vec::new(); let mut targets=Vec::new();
            for _ in 0..ns { starts.push(scanner.integer()?); }
            for _ in 0..nt { let value:Int=scanner.integer()?; if value<1 || value>limit { return Err("invalid LNS target".into()); } targets.push(value); }
            let count:usize=scanner.integer()?; if count>4096 { return Err("invalid LNS hint length".into()); }
            let mut hint=Vec::new();
            for _ in 0..count {
                let token=scanner.next()?.ok_or("truncated LNS hint")?;
                if token.len()!=1 { return Err("invalid LNS operator".into()); }
                hint.push([token.as_bytes()[0] as i32,scanner.integer()?,scanner.integer()?,scanner.integer()?]);
            }
            let puzzle=Puzzle { limit,starts,targets,definitions:BTreeMap::new(),arguments:BTreeMap::new(),order:Vec::new() };
            let archive_path=env::var_os("SANTA2026_LNS_ARCHIVE").map(std::path::PathBuf::from);
            let (program,m,archive)=optimize_capture(puzzle,&hint,seed,milliseconds,flags,archive_path.is_some())?;
            if let Some(path)=archive_path {
                if let Err(error)=write_archive(&path,&id,&archive) { eprintln!("LNS archive error: {}",error); }
            }
            write!(writer,"{} [",id).map_err(|e|e.to_string())?;
            for (index,step) in program.iter().enumerate() {
                if index!=0 { write!(writer,",").map_err(|e|e.to_string())?; }
                write!(writer,"[\"{}\",{},{},{}]",step[0] as u8 as char,step[1],step[2],step[3]).map_err(|e|e.to_string())?;
            }
            writeln!(writer,"]").map_err(|e|e.to_string())?; writer.flush().map_err(|e|e.to_string())?;
            eprintln!("{{\"lns\":\"{}\",\"baseline\":{},\"best\":{},\"attempts\":{},\"helper_attempts\":{},\"accepted\":{},\"plateaus\":{},\"kicks\":{},\"improvements\":{},\"catalogs\":{},\"max_pool\":{},\"max_s1\":{},\"need_aborts\":{},\"deadline_aborts\":{},\"skipped_identity\":{},\"frontier_cuts\":{},\"frontier_orders\":{},\"frontier_max_ports\":{},\"frontier_wins\":{},\"target_blocks\":{},\"target_block_orders\":{},\"target_block_wins\":{}}}",
                id,hint.len(),program.len(),m.attempts,m.helper_attempts,m.accepted,m.plateaus,m.kicks,m.improvements,m.catalogs,m.max_pool,m.max_s1,m.need_aborts,m.deadline_aborts,m.skipped_identity,m.frontier_cuts,m.frontier_orders,m.frontier_ports,m.frontier_wins,m.target_blocks,m.target_block_orders,m.target_block_wins);
        }
        Ok(())
    }

    #[cfg(test)]
    mod lns_tests {
        use super::*;
        fn puzzle(starts:Vec<Int>,targets:Vec<Int>,limit:Int)->Puzzle {
            Puzzle { limit,starts,targets,definitions:BTreeMap::new(),arguments:BTreeMap::new(),order:Vec::new() }
        }
        fn search(pool:Vec<Int>,limit:Int)->Searcher { Searcher::new(pool,limit,Instant::now()+Duration::from_secs(5)).unwrap() }

        #[test]
        fn final_division_can_visit_values_above_target() {
            let mut s=search(vec![17,19,5,2],1000);
            let steps=s.find(159,3,false,&mut Metrics::default()).unwrap();
            assert!(steps.len()<=3); assert!(steps.iter().any(|s|s.value>159));
            assert_eq!(s.normalize(steps,159,3).unwrap().last().unwrap().value,159);
        }

        #[test]
        fn shared_helper_cone_excludes_descendants_and_preserves_consumers() {
            let mut p=puzzle(vec![2,3],vec![19,24],100);
            p.load_hint(&vec![[b'+' as i32,0,1,2],[b'*' as i32,2,1,3],
                [b'+' as i32,3,0,4],[b'+' as i32,4,0,5],[b'+' as i32,3,1,6],
                [b'+' as i32,6,1,7],[b'+' as i32,7,1,8]]).unwrap();
            // 15 is shared by both target paths; 5 and 15 form its private cone.
            let cone=exclusive_cone(&p,15).unwrap(); assert_eq!(cone,BTreeSet::from([5,15]));
            let pool=legal_pool(&p,15,&cone);
            assert!(!pool.contains(&19)); assert!(!pool.contains(&24)); assert!(!pool.contains(&17));
            let bad=vec![Step{op:b'-',a:17,b:2,value:15}];
            assert!(rebuild(&p,&cone,15,&bad).is_err());
        }

        #[test]
        fn plateau_and_kick_never_replace_saved_best_with_longer_program() {
            let p=puzzle(vec![2,3],vec![],100); let mut m=Metrics::default();
            let mut walk=Walk { current:p.clone(),current_cost:2,best:vec![[0;4];2],best_graph:p.clone(),seen:HashSet::new(),archive:None };
            let mut equal=p.clone(); equal.make(b'+',2,3).unwrap();
            assert!(walk.accept(equal,vec![[0;4];2],true,false,&mut m));
            let mut longer=p.clone(); longer.make(b'*',2,3).unwrap();
            assert!(walk.accept(longer,vec![[0;4];4],true,true,&mut m));
            assert_eq!(walk.best.len(),2); assert_eq!(walk.current_cost,4);
            let mut too_long=p; too_long.make(b'*',3,3).unwrap();
            assert!(!walk.accept(too_long,vec![[0;4];5],true,true,&mut m));
            assert_eq!(walk.best.len(),2); assert_eq!(m.plateaus,1); assert_eq!(m.kicks,1);
        }

        #[test]
        fn near_miss_accepts_free_residual_and_counts_shared_operand_once() {
            let mut s=search(vec![11,13,7],1000);
            let xs=s.c01(24).unwrap();
            let out=s.consider_product(271,24,11,&xs,&[],3).unwrap();
            assert_eq!(out.len(),3); // 24=11+13; 264=24*11; 271=264+7.
            let shared=s.c01(24).unwrap();
            let out=s.consider_product(576,24,24,&shared,&shared,2).unwrap();
            assert_eq!(out.len(),2);
        }

        #[test]
        fn kick_search_can_skip_the_existing_shortest_route() {
            let mut s=search(vec![2,3,7],1000); s.minimum=3;
            let steps=s.find(5,3,false,&mut Metrics::default()).unwrap();
            assert_eq!(steps.len(),3); assert_eq!(steps.last().unwrap().value,5);
        }

        #[test]
        fn complete_identity_plan_is_skipped_without_rejecting_its_prefix() {
            let mut s=search(vec![2,3,7],1000); s.root=11;
            s.identity=vec![(9,b'*',3,3),(11,b'+',2,9)];
            // The old first match is 9=3*3; 11=9+2. Keep searching for 14-3.
            let steps=s.find(11,2,false,&mut Metrics::default()).unwrap();
            assert_eq!(steps.len(),2); assert!(steps.iter().any(|step|step.value==14));
            assert!(s.skipped_identity>0);
            // A matching old partial helper remains legal for other plans.
            assert!(s.cost2(9).is_some());
        }

        #[test]
        fn one_op_identity_filter_still_finds_an_equal_alternative() {
            let mut s=search(vec![2,3,7],1000); s.root=5;
            s.identity=vec![(5,b'+',2,3)];
            let steps=s.find(5,1,false,&mut Metrics::default()).unwrap();
            assert_eq!(steps,vec![Step{op:b'-',a:7,b:2,value:5}]);
        }

        #[test]
        fn two_op_residual_pays_a_shared_product_operand_once() {
            let mut s=search(vec![11,13,7],1000); let shared=s.c01(24).unwrap();
            // R=22=(11+11) and not a two-op residual, so choose R=26+7=33.
            // 26=13+13; 33=26+7 costs two, while X=Y=24 costs one.
            let steps=s.consider_product(609,24,24,&shared,&shared,5).unwrap();
            assert_eq!(steps.len(),5); assert_eq!(steps.last().unwrap().value,609);
        }

        #[test]
        fn arithmetic_stays_exact_and_bounded() {
            let limit=1_000_000_000_000_000_000;
            assert_eq!(checked(b'/',5,2,limit),None);
            assert_eq!(checked(b'-',2,2,limit),None);
            assert_eq!(checked(b'*',limit,limit,limit),None);
            assert_eq!(checked(b'*',1_000_000_000,1_000_000_000,limit),Some(limit));
            assert!(inverses(159,2,1000).iter().any(|v|v.value==318&&v.op==b'/'));
        }

        #[test]
        fn physical_allocator_can_release_dead_start_registers() {
            let mut p=puzzle((1..=48).collect(),vec![49],100);
            let hint=vec![[b'+' as i32,0,47,0]];
            p.load_hint(&hint).unwrap();
            let out=verified_program(&p).unwrap(); assert_eq!(out.len(),1);
            p.load_hint(&out).unwrap();
        }

        #[test]
        fn frontier_retires_all_four_consumers_atomically() {
            let p=puzzle(vec![3,5,7,11,14,28,41,43,47,53],vec![67,69,71,75],1000);
            let hint=vec![[b'-' as i32,2,0,10],[b'*' as i32,10,10,11],
                [b'*' as i32,11,10,12],[b'+' as i32,12,0,13],
                [b'+' as i32,12,1,14],[b'+' as i32,12,2,15],[b'+' as i32,12,3,16]];
            for flags in [16,20,31] {
                let (out,m)=optimize(p.clone(),&hint,0,500,flags).unwrap();
                assert_eq!(out.len(),4); assert_eq!(m.frontier_wins,1);
                assert_eq!(m.frontier_ports,4);
                let mut replay=p.clone(); replay.load_hint(&out).unwrap();
                assert!(!replay.definitions.contains_key(&64));
            }
        }

        #[test]
        fn frontier_pays_new_shared_helper_once() {
            let p=puzzle(vec![2,5],vec![14,35],100);
            let hint=vec![[b'+' as i32,0,0,2],[b'+' as i32,1,1,3],
                [b'+' as i32,3,2,4],[b'*' as i32,1,1,5],[b'+' as i32,5,3,6]];
            let (out,m)=optimize(p.clone(),&hint,1,500,16).unwrap();
            assert_eq!(out.len(),3); assert!(m.frontier_wins>0);
            let mut replay=p; replay.load_hint(&out).unwrap();
            assert!(replay.definitions.contains_key(&7));
        }

        #[test]
        fn frontier_context_blocks_descendant_cycles_and_keeps_seeds() {
            let mut p=puzzle(vec![2,5],vec![14,35,40],100);
            let hint=vec![[b'+' as i32,0,0,2],[b'+' as i32,1,1,3],
                [b'+' as i32,3,2,4],[b'*' as i32,1,1,5],[b'+' as i32,5,3,6],
                [b'+' as i32,6,1,7]];
            p.load_hint(&hint).unwrap();
            let (removed,pool)=frontier_context(&p,&[14,35]).unwrap();
            assert!(removed.contains(&10)); assert!(removed.contains(&25));
            assert!(!pool.contains(&40)); assert!(!pool.contains(&14));
            assert!(pool.contains(&2) && pool.contains(&5));
            let bad=vec![Step{op:b'-',a:40,b:5,value:35}];
            assert!(rebuild(&p,&removed,35,&bad).is_err());
        }

        #[test]
        fn sampled_target_blocks_are_bounded_distinct_and_only_actual_targets() {
            let mut p=puzzle((1..=48).collect(),(49..=80).collect(),100);
            let hint=(0..32).map(|index|vec![b'+' as i32,index,47,46])
                .map(|step|[step[0],step[1],step[2],step[3]]).collect();
            p.load_hint(&hint).unwrap();
            let blocks=sampled_target_blocks(&p,&mut Mt19937_64::new(9),MAX_TARGET_BLOCKS);
            assert!(!blocks.is_empty() && blocks.len()<=MAX_TARGET_BLOCKS);
            let mut seen=BTreeSet::new();
            for mut block in blocks {
                assert!([3,4,6,8].contains(&block.len()));
                assert!(block.iter().all(|v|p.targets.contains(v)));
                block.sort_unstable(); assert!(seen.insert(block));
            }
            assert!(sampled_target_blocks(&p,&mut Mt19937_64::new(9),0).is_empty());
        }

        fn target_block_fixture()->(Puzzle,Program) {
            let p=puzzle(vec![2,3,11,17,23,31],vec![161,583,986],2000);
            let hint=vec![[b'*' as i32,2,2,6],[b'+' as i32,4,3,7],[b'+' as i32,6,7,8],
                [b'*' as i32,4,4,9],[b'+' as i32,5,4,10],[b'+' as i32,9,10,11],
                [b'*' as i32,3,0,12],[b'-' as i32,5,0,13],[b'*' as i32,12,13,14]];
            (p,hint)
        }

        #[test]
        fn actual_target_block_removes_three_private_cones_and_pays_shared_helper() {
            let (mut p,hint)=target_block_fixture(); p.load_hint(&hint).unwrap();
            let (removed,pool)=frontier_context(&p,&p.targets).unwrap();
            assert_eq!(removed.len(),9); assert_eq!(pool,BTreeSet::from([2,3,11,17,23,31]).into_iter().collect::<Vec<_>>());
            let steps=vec![Step{op:b'+',a:2,b:3,value:5},Step{op:b'*',a:5,b:11,value:55},
                Step{op:b'+',a:5,b:2,value:7},Step{op:b'*',a:7,b:23,value:161},
                Step{op:b'-',a:55,b:2,value:53},Step{op:b'*',a:53,b:11,value:583},
                Step{op:b'+',a:55,b:3,value:58},Step{op:b'*',a:58,b:17,value:986}];
            let trial=rebuild(&p,&removed,986,&steps).unwrap();
            assert_eq!(verified_program(&trial).unwrap().len(),8);
        }

        #[test]
        fn target_block_frontier_and_depth_flag_leave_legacy_walk_disabled() {
            let (p,hint)=target_block_fixture();
            for flags in [16,20] {
                let (program,metrics)=optimize(p.clone(),&hint,0,100,flags).unwrap();
                assert!(program.len()<=hint.len()); assert_eq!(metrics.attempts,0);
                assert!(metrics.target_blocks>0 && metrics.target_blocks<=MAX_TARGET_BLOCKS);
                assert!(metrics.target_block_orders>0);
                let mut replay=p.clone(); replay.load_hint(&program).unwrap();
            }
            let (program,metrics)=optimize(p.clone(),&hint,0,1,16).unwrap();
            assert!(program.len()<=hint.len()); assert_eq!(metrics.attempts,0);
            let mut replay=p; replay.load_hint(&program).unwrap();
        }

        #[test]
        fn archive_keeps_eight_distinct_valid_graphs_and_applies_best_cost_filter() {
            let mut archive=Archive::default();
            for index in 0..10 {
                let mut p=puzzle((1..=48).collect(),vec![49],100);
                let hint=vec![[b'+' as i32,index,47-index,index]];
                p.load_hint(&hint).unwrap(); let program=verified_program(&p).unwrap();
                archive.consider(&p,&program,1);
            }
            assert_eq!(archive.entries.len(),8);
            for entry in &archive.entries {
                let mut replay=puzzle((1..=48).collect(),vec![49],100);
                replay.load_hint(&entry.program).unwrap();
            }
            archive.entries.push(ArchiveEntry{program:vec![[0;4];4],recipes:BTreeSet::new()});
            archive.prune(1); assert_eq!(archive.entries.len(),8);
        }

        #[test]
        fn sidecar_is_atomic_utf8_json_with_exact_instruction_symbols() {
            let path=std::env::temp_dir().join(format!("santa-lns-archive-{}.json",std::process::id()));
            let programs=vec![vec![[b'/' as i32,0,1,2]]];
            write_archive(&path,"p\"uzzle",&programs).unwrap();
            let text=std::fs::read_to_string(&path).unwrap();
            assert_eq!(text,"{\"version\":1,\"id\":\"p\\\"uzzle\",\"programs\":[[[\"/\",0,1,2]]]}");
            assert!(!std::path::PathBuf::from(format!("{}.tmp",path.display())).exists());
            std::fs::remove_file(path).unwrap();
        }

        #[test]
        fn soft_deadline_returns_valid_baseline() {
            let p=puzzle(vec![3],vec![1,2,5,8],9);
            let hint=vec![[b'/' as i32,0,0,1],[b'-' as i32,0,1,2],[b'+' as i32,0,2,3],[b'+' as i32,0,3,4]];
            let (out,_)=optimize(p.clone(),&hint,0,1,15).unwrap();
            assert!(out.len()<=hint.len()); let mut replay=p; replay.load_hint(&out).unwrap();
        }
    }
}

fn main() { if let Err(error)=engine::run_lns() { eprintln!("Solver error: {}",error); std::process::exit(1); } }
