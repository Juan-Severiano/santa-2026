// SPDX-License-Identifier: Apache-2.0
// Rust port of the frozen mode-6 dictionary/beam hint repair engine.
// Original solver: Tony Li, https://www.kaggle.com/code/tonylica/elf-calculator-with-register-reuse
// Parameterized portfolio: konbu17,
// https://www.kaggle.com/code/konbu17/santa-2026-wider-dictionary-portfolio-163k
// Local modification: same-work Rust implementation, preserving mode-6 order,
// dictionary insertion, beam ties, libc++ shuffle, and 48-register allocation.
// See licenses/Apache-2.0.txt and THIRD_PARTY.md.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::env;
use std::io::{self, BufRead, BufReader, BufWriter, Write};

type Int = i64;
type Program = Vec<[i32; 4]>;
type Outcome<T> = Result<T, String>;

#[derive(Clone)]
struct Config {
    width: usize,
    alpha: f64,
    n_done: usize,
    n_recent: usize,
    bits: usize,
    seed: u64,
    trials: usize,
    passes: usize,
}

#[derive(Clone, Copy, Default)]
#[repr(C)]
struct Expr {
    value: Int,
    a: i32,
    b: i32,
    op: u8,
    cost: i32,
}

struct Index {
    keys: Vec<Int>,
    ids: Vec<i32>,
    used: Vec<u32>,
    mask: usize,
}

impl Index {
    fn new(bits: usize) -> Self {
        let size = 1usize << bits;
        Self { keys: vec![0; size], ids: vec![0; size], used: Vec::new(), mask: size - 1 }
    }
    #[inline]
    fn slot(&self, value: Int) -> usize {
        let mut hash = (value as u64).wrapping_mul(0x9e3779b97f4a7c15);
        hash ^= hash >> 31;
        let mut position = hash as usize & self.mask;
        while self.keys[position] != 0 && self.keys[position] != value {
            position = (position + 1) & self.mask;
        }
        position
    }
    #[inline]
    fn get(&self, value: Int) -> i32 {
        let position = self.slot(value);
        if self.keys[position] != 0 { self.ids[position] } else { -1 }
    }
    fn insert(&mut self, value: Int, id: i32) -> bool {
        let position = self.slot(value);
        if self.keys[position] != 0 || self.used.len() * 8 > self.keys.len() * 7 {
            return false;
        }
        self.used.push(position as u32);
        self.keys[position] = value;
        self.ids[position] = id;
        true
    }
    fn clear(&mut self) {
        for &position in &self.used {
            self.keys[position as usize] = 0;
        }
        self.used.clear();
    }
}

struct Dictionary {
    limit: Int,
    index: Index,
    expressions: Vec<Expr>,
    sorted: [Vec<(Int, i32)>; 3],
    n0: usize,
    n1: usize,
}

impl Dictionary {
    fn new(limit: Int, bits: usize) -> Self {
        Self { limit, index: Index::new(bits), expressions: Vec::new(),
               sorted: [Vec::new(), Vec::new(), Vec::new()], n0: 0, n1: 0 }
    }
    fn add(&mut self, value: Int, a: i32, b: i32, op: u8, cost: i32) {
        if value > 0 && value <= self.limit && self.index.insert(value, self.expressions.len() as i32) {
            self.expressions.push(Expr { value, a, b, op, cost });
        }
    }
    fn combine(&mut self, i: usize, j: usize, cost: i32) {
        let a = self.expressions[i].value;
        let b = self.expressions[j].value;
        self.add(a + b, i as i32, j as i32, b'+', cost);
        if a != b {
            self.add((a - b).abs(), if a > b { i } else { j } as i32,
                     if a > b { j } else { i } as i32, b'-', cost);
        }
        if a <= self.limit / b { self.add(a * b, i as i32, j as i32, b'*', cost); }
        if a > b && a % b == 0 { self.add(a / b, i as i32, j as i32, b'/', cost); }
        if b > a && b % a == 0 { self.add(b / a, j as i32, i as i32, b'/', cost); }
    }
    fn build(&mut self, leaves: &[Int]) {
        self.index.clear();
        self.expressions.clear();
        for values in &mut self.sorted { values.clear(); }
        for &value in leaves { self.add(value, -1, -1, 0, 0); }
        self.n0 = self.expressions.len();
        for i in 0..self.n0 {
            for j in 0..=i { self.combine(i, j, 1); }
        }
        self.n1 = self.expressions.len();
        for i in self.n0..self.n1 {
            for j in 0..self.n0 { self.combine(i, j, 2); }
        }
        for (i, expression) in self.expressions.iter().enumerate() {
            self.sorted[expression.cost as usize].push((expression.value, i as i32));
        }
        for values in &mut self.sorted { values.sort_unstable(); }
    }
}

#[derive(Clone, Copy)]
#[repr(C)]
struct Link { factor: i32, atom: i32, op: u8 }

impl Default for Link {
    fn default() -> Self { Self { factor: -1, atom: -1, op: b'+' } }
}

#[derive(Clone, Copy)]
#[repr(C)]
struct State { value: Int, cost: i32, parent: i32, link: Link, rank: f64 }

struct Search<'a> {
    dictionary: &'a Dictionary,
    width: usize,
    alpha: f64,
    best: i32,
    end: i32,
    terminal: i32,
    history: Vec<State>,
    next: Vec<State>,
    visited: HashMap<Int, i32>,
}

fn lower_bound(values: &[(Int, i32)], value: Int) -> usize {
    values.partition_point(|&(number, id)| number < value || (number == value && id < -1))
}

impl<'a> Search<'a> {
    fn new(dictionary: &'a Dictionary, config: &Config) -> Self {
        Self { dictionary, width: config.width, alpha: config.alpha, best: 1000, end: -1,
               terminal: -1, history: Vec::new(), next: Vec::new(), visited: HashMap::new() }
    }
    fn expression_cost(&self, id: i32) -> i32 {
        if id < 0 { 0 } else { 1 + self.dictionary.expressions[id as usize].cost }
    }
    fn offer(&mut self, parent: usize, value: Int, factor: i32, atom: i32, op: u8) {
        if value <= 0 || value > self.dictionary.limit { return; }
        let cost = self.history[parent].cost + self.expression_cost(factor) + self.expression_cost(atom);
        if cost >= self.best { return; }
        let terminal = self.dictionary.index.get(value);
        if terminal >= 0 {
            if cost + self.dictionary.expressions[terminal as usize].cost < self.best {
                self.best = cost + self.dictionary.expressions[terminal as usize].cost;
                self.end = self.history.len() as i32;
                self.terminal = terminal;
                self.history.push(State { value, cost, parent: parent as i32,
                    link: Link { factor, atom, op }, rank: 0.0 });
            }
            return;
        }
        if cost + 3 >= self.best || value >= self.history[parent].value { return; }
        let rank = cost as f64 + self.alpha * (value as f64).log2();
        if self.next.len() == self.width && rank >= self.next.last().unwrap().rank { return; }
        if self.visited.get(&value).is_some_and(|&previous| previous <= cost) { return; }
        if let Some(position) = self.next.iter().position(|state| state.value == value) {
            if self.next[position].cost <= cost { return; }
            self.next.remove(position);
        }
        let state = State { value, cost, parent: parent as i32, link: Link { factor, atom, op }, rank };
        let position = self.next.partition_point(|state| state.rank <= rank);
        self.next.insert(position, state);
        if self.next.len() > self.width { self.next.pop(); }
    }
    fn shifted(&mut self, parent: usize, difference: Int, atom: i32, op: u8) {
        let mut exponent = (difference as u64).trailing_zeros() as i32;
        while exponent >= 4 {
            let factor = self.dictionary.index.get(1i64 << exponent);
            if factor >= 0 {
                self.offer(parent, difference >> exponent, factor, atom, op);
                break;
            }
            exponent -= 1;
        }
    }
    fn expand(&mut self, parent: usize) {
        let value = self.history[parent].value;
        for group in 0..3 {
            let center = lower_bound(&self.dictionary.sorted[group], value) as isize;
            for offset in -4..4 {
                let position = center + offset;
                if position < 0 || position as usize >= self.dictionary.sorted[group].len() { continue; }
                let (atom, id) = self.dictionary.sorted[group][position as usize];
                self.offer(parent, (value - atom).abs(), -1, id, if value > atom { b'+' } else { b'r' });
            }
        }
        for i in 0..self.dictionary.n1 {
            let atom = self.dictionary.expressions[i].value;
            if atom == 1 { continue; }
            let difference = (value - atom).abs();
            if difference != 0 {
                if self.dictionary.index.get(difference) >= 0 {
                    self.offer(parent, difference, -1, i as i32, if value > atom { b'+' } else { b'r' });
                }
                if value > atom && difference & 15 == 0 { self.shifted(parent, difference, i as i32, b'+'); }
                if atom > value && difference & 15 == 0 { self.shifted(parent, difference, i as i32, b'r'); }
            }
            if atom + value <= self.dictionary.limit {
                if self.dictionary.index.get(atom + value) >= 0 {
                    self.offer(parent, atom + value, -1, i as i32, b'-');
                }
                if (atom + value) & 15 == 0 { self.shifted(parent, atom + value, i as i32, b'-'); }
            }
            if atom >= value { continue; }
            let quotient = value / atom;
            let remainder = value % atom;
            if remainder == 0 {
                self.offer(parent, quotient, i as i32, -1, b'+');
                continue;
            }
            let remainder_id = self.dictionary.index.get(remainder);
            if remainder_id >= 0 { self.offer(parent, quotient, i as i32, remainder_id, b'+'); }
            if atom - remainder <= self.dictionary.limit - value {
                let id = self.dictionary.index.get(atom - remainder);
                if id >= 0 { self.offer(parent, quotient + 1, i as i32, id, b'-'); }
            }
            if i < self.dictionary.n0 && quotient > 64 {
                for group in 0..3 {
                    let center = lower_bound(&self.dictionary.sorted[group], quotient) as isize;
                    for offset in -1..=0 {
                        let position = center + offset;
                        if position < 0 || position as usize >= self.dictionary.sorted[group].len() { continue; }
                        let (other, _) = self.dictionary.sorted[group][position as usize];
                        if other > self.dictionary.limit / atom { continue; }
                        let product = atom * other;
                        if product == value { continue; }
                        let id = self.dictionary.index.get(product);
                        if id >= 0 {
                            self.offer(parent, (product - value).abs(), -1, id,
                                       if product < value { b'+' } else { b'r' });
                        }
                    }
                }
            }
        }
    }
    fn run(&mut self, target: Int) {
        self.history.push(State { value: target, cost: 0, parent: -1, link: Link::default(), rank: 0.0 });
        let id = self.dictionary.index.get(target);
        if id >= 0 {
            self.end = 0;
            self.terminal = id;
            self.best = self.dictionary.expressions[id as usize].cost;
            return;
        }
        let mut front = vec![0usize];
        for _ in 0..20 {
            if front.is_empty() { break; }
            self.next.clear();
            for &parent in &front {
                if self.history[parent].cost + 3 < self.best { self.expand(parent); }
            }
            front.clear();
            for state in &self.next {
                if state.cost + 3 >= self.best { continue; }
                self.visited.insert(state.value, state.cost);
                front.push(self.history.len());
                self.history.push(*state);
            }
        }
    }
}

// Standard MT19937-64 and the forward Fisher-Yates/rejection schedule used by
// Apple libc++ std::shuffle. Native language RNGs would change fixed-work tests.
struct Mt19937_64 { state: [u64; 312], position: usize }

impl Mt19937_64 {
    fn new(seed: u64) -> Self {
        let mut generator = Self { state: [0; 312], position: 312 };
        generator.state[0] = seed;
        for i in 1..312 {
            generator.state[i] = 6364136223846793005u64
                .wrapping_mul(generator.state[i - 1] ^ (generator.state[i - 1] >> 62)).wrapping_add(i as u64);
        }
        generator
    }
    fn next(&mut self) -> u64 {
        if self.position == 312 {
            for i in 0..312 {
                let mixed = (self.state[i] & 0xffffffff80000000) | (self.state[(i + 1) % 312] & 0x7fffffff);
                self.state[i] = self.state[(i + 156) % 312] ^ (mixed >> 1)
                    ^ if mixed & 1 != 0 { 0xb5026f5aa96619e9 } else { 0 };
            }
            self.position = 0;
        }
        let mut value = self.state[self.position];
        self.position += 1;
        value ^= (value >> 29) & 0x5555555555555555;
        value ^= (value << 17) & 0x71d67fffeda60000;
        value ^= (value << 37) & 0xfff7eee000000000;
        value ^= value >> 43;
        value
    }
    fn below(&mut self, range: u64) -> u64 {
        if range <= 1 { return 0; }
        let bits = 64 - (range - 1).leading_zeros();
        let mask = if bits == 64 { u64::MAX } else { (1u64 << bits) - 1 };
        loop {
            let value = self.next() & mask;
            if value < range { return value; }
        }
    }
    fn shuffle<T>(&mut self, values: &mut [T]) {
        for first in 0..values.len().saturating_sub(1) {
            let offset = self.below((values.len() - first) as u64) as usize;
            if offset != 0 { values.swap(first, first + offset); }
        }
    }
}

#[derive(Clone)]
struct Puzzle {
    limit: Int,
    starts: Vec<Int>,
    targets: Vec<Int>,
    definitions: BTreeMap<Int, Expr>,
    arguments: BTreeMap<Int, (Int, Int)>,
    order: Vec<Int>,
}

fn apply(op: u8, a: Int, b: Int) -> Outcome<Int> {
    match op {
        b'+' => Ok(a + b),
        b'-' => Ok(a - b),
        b'/' => if b != 0 { Ok(a / b) } else { Err("zero divisor".into()) },
        b'*' => a.checked_mul(b).ok_or_else(|| "integer overflow".into()),
        _ => Err("invalid operator".into()),
    }
}

impl Puzzle {
    fn make(&mut self, op: u8, a: Int, b: Int) -> Outcome<Int> {
        let value = apply(op, a, b)?;
        if value <= 0 || value > self.limit { return Err("invalid construction".into()); }
        if !self.definitions.contains_key(&value) {
            self.definitions.insert(value, Expr { value, a: -1, b: -1, op, cost: 0 });
            self.arguments.insert(value, (a, b));
            self.order.push(value);
        }
        Ok(value)
    }
    fn load_hint(&mut self, hint: &Program) -> Outcome<()> {
        if self.starts.is_empty() || self.starts.len() > 48 || hint.len() > 4096 {
            return Err("invalid hint dimensions".into());
        }
        self.definitions.clear(); self.arguments.clear(); self.order.clear();
        let mut bank = [0i64; 48];
        let mut covered = BTreeSet::new();
        for (i, &value) in self.starts.iter().enumerate() {
            if value <= 0 || value > self.limit { return Err("invalid initial hint value".into()); }
            bank[i] = value;
            self.definitions.insert(value, Expr { value, a: -1, b: -1, op: 0, cost: 0 });
            covered.insert(value);
        }
        for &[operation, left, right, destination] in hint {
            if !(0..48).contains(&left) || !(0..48).contains(&right) || !(0..48).contains(&destination) {
                return Err("invalid hint register".into());
            }
            let (a, b) = (bank[left as usize], bank[right as usize]);
            if a == 0 || b == 0 { return Err("undefined hint source".into()); }
            let op = operation as u8;
            if ![b'+', b'-', b'*', b'/'].contains(&op) { return Err("invalid hint operator".into()); }
            if (op == b'-' && a <= b) || (op == b'/' && a % b != 0) || (op == b'*' && a > self.limit / b) {
                return Err("illegal hint arithmetic".into());
            }
            let value = self.make(op, a, b)?;
            bank[destination as usize] = value;
            covered.insert(value);
        }
        if self.targets.iter().any(|value| !covered.contains(value)) {
            return Err("hint does not cover every target".into());
        }
        Ok(())
    }
    fn descendants(&self, target: Int) -> BTreeSet<Int> {
        let mut users: BTreeMap<Int, Vec<Int>> = BTreeMap::new();
        for (&value, &(a, b)) in &self.arguments {
            users.entry(a).or_default().push(value);
            if a != b { users.entry(b).or_default().push(value); }
        }
        let mut blocked = BTreeSet::from([target]);
        let mut queue = vec![target];
        let mut cursor = 0;
        while cursor < queue.len() {
            if let Some(values) = users.get(&queue[cursor]) {
                for &value in values {
                    if blocked.insert(value) { queue.push(value); }
                }
            }
            cursor += 1;
        }
        blocked
    }
    fn materialize(&mut self, dictionary: &Dictionary, id: i32) -> Outcome<Int> {
        let expression = dictionary.expressions[id as usize];
        if self.definitions.contains_key(&expression.value) { return Ok(expression.value); }
        if expression.a < 0 || expression.b < 0 { return Err("unavailable dictionary leaf".into()); }
        let right = self.materialize(dictionary, expression.b)?;
        let left = self.materialize(dictionary, expression.a)?;
        self.make(expression.op, left, right)
    }
    fn reconstruct(&mut self, dictionary: &Dictionary, search: &Search, target: Int) -> Outcome<()> {
        let mut value = self.materialize(dictionary, search.terminal)?;
        let mut position = search.end;
        while search.history[position as usize].parent >= 0 {
            let state = search.history[position as usize];
            if state.link.factor >= 0 {
                let factor = self.materialize(dictionary, state.link.factor)?;
                value = self.make(b'*', value, factor)?;
            }
            if state.link.atom >= 0 {
                let atom = self.materialize(dictionary, state.link.atom)?;
                value = if state.link.op == b'r' { self.make(b'-', atom, value)? }
                        else { self.make(state.link.op, value, atom)? };
            }
            position = state.parent;
        }
        if value != target { return Err("replanned search reconstruction failed".into()); }
        Ok(())
    }
    fn compile(&self) -> Outcome<Program> {
        fn visit(puzzle: &Puzzle, value: Int, placed: &mut BTreeSet<Int>, sequence: &mut Vec<Int>) -> Outcome<()> {
            if !placed.insert(value) { return Ok(()); }
            let &(a, b) = puzzle.arguments.get(&value).ok_or_else(|| "missing value recipe".to_string())?;
            visit(puzzle, a, placed, sequence)?; visit(puzzle, b, placed, sequence)?;
            sequence.push(value); Ok(())
        }
        fn emit(puzzle: &Puzzle, value: Int, locked: u64, bank: &mut [Int; 48],
                seeds: &BTreeSet<Int>, uses: &mut BTreeMap<Int, Vec<usize>>, program: &mut Program) -> Outcome<usize> {
            if let Some(position) = bank.iter().position(|&item| item == value) { return Ok(position); }
            let &(a, b) = puzzle.arguments.get(&value).ok_or_else(|| "missing register recipe".to_string())?;
            let ra = emit(puzzle, a, locked, bank, seeds, uses, program)?;
            let rb = emit(puzzle, b, locked | (1u64 << ra), bank, seeds, uses, program)?;
            let mut destination = None;
            let mut latest = -1i64;
            for k in 0..48 {
                let old = bank[k];
                if locked >> k & 1 != 0 || seeds.contains(&old) { continue; }
                let next_use = uses.entry(old).or_default().last().map_or(1_000_000, |&position| position as i64);
                if old == 0 { destination = Some(k); break; }
                if next_use > latest { destination = Some(k); latest = next_use; }
            }
            let rd = destination.ok_or_else(|| "register allocation failed".to_string())?;
            bank[rd] = value;
            let expression = puzzle.definitions.get(&value).ok_or_else(|| "missing expression".to_string())?;
            program.push([expression.op as i32, ra as i32, rb as i32, rd as i32]);
            Ok(rd)
        }
        let seeds: BTreeSet<Int> = self.starts.iter().copied().collect();
        let mut placed = seeds.clone();
        let mut sequence = Vec::new();
        for &target in &self.targets { visit(self, target, &mut placed, &mut sequence)?; }
        let mut uses: BTreeMap<Int, Vec<usize>> = BTreeMap::new();
        for i in (0..sequence.len()).rev() {
            let &(a, b) = self.arguments.get(&sequence[i]).ok_or_else(|| "missing scheduled recipe".to_string())?;
            uses.entry(a).or_default().push(i);
            if a != b { uses.entry(b).or_default().push(i); }
        }
        let mut bank = [0; 48];
        bank[..self.starts.len()].copy_from_slice(&self.starts);
        let mut program = Vec::new();
        for value in sequence {
            let (a, b) = self.arguments[&value];
            uses.entry(a).or_default().pop();
            if a != b { uses.entry(b).or_default().pop(); }
            emit(self, value, 0, &mut bank, &seeds, &mut uses, &mut program)?;
        }
        Ok(program)
    }
    fn polish_hint(&mut self, hint: &Program, config: &Config) -> Outcome<Program> {
        self.load_hint(hint)?;
        let mut best_program = hint.clone();
        if let Ok(normalized) = self.compile() {
            if normalized.len() < best_program.len() { best_program = normalized; }
        }
        let mut dictionary = Dictionary::new(self.limit, config.bits);
        let mut generator = Mt19937_64::new(config.seed);
        for _ in 0..config.passes {
            let mut target_uses: BTreeMap<Int, i32> = BTreeMap::new();
            let mut fanout: BTreeMap<Int, i32> = BTreeMap::new();
            let mut ancestors: BTreeMap<Int, BTreeSet<Int>> = BTreeMap::new();
            for &(a, b) in self.arguments.values() {
                *fanout.entry(a).or_default() += 1;
                *fanout.entry(b).or_default() += 1;
            }
            fn visit(puzzle: &Puzzle, value: Int, ancestors: &mut BTreeSet<Int>, uses: &mut BTreeMap<Int, i32>) {
                if !ancestors.insert(value) { return; }
                *uses.entry(value).or_default() += 1;
                if let Some(&(a, b)) = puzzle.arguments.get(&value) {
                    visit(puzzle, a, ancestors, uses); visit(puzzle, b, ancestors, uses);
                }
            }
            for &target in &self.targets {
                visit(self, target, ancestors.entry(target).or_default(), &mut target_uses);
            }
            let mut ranked = Vec::new();
            for &target in &self.targets {
                if !self.arguments.contains_key(&target) { continue; }
                let exclusive = ancestors[&target].iter().filter(|value| self.arguments.contains_key(value)
                    && target_uses.get(value).copied().unwrap_or(0) == 1).count() as i32;
                ranked.push((-exclusive, target));
            }
            ranked.sort_unstable();
            if config.seed % 3 == 1 { generator.shuffle(&mut ranked); }
            else if config.seed % 3 == 2 {
                ranked.sort_by(|a, b| ancestors[&b.1].len().cmp(&ancestors[&a.1].len()));
            }
            let attempts = ranked.len().min(config.trials);
            let mut improved = false;
            for &(_, target) in ranked.iter().take(attempts) {
                let blocked = self.descendants(target);
                let mut leaves = Vec::new();
                let mut used = BTreeSet::new();
                let mut add = |value: Int| {
                    if self.definitions.contains_key(&value) && !blocked.contains(&value) && used.insert(value) {
                        leaves.push(value);
                    }
                };
                for &value in &self.starts { add(value); }
                add(1);
                for exponent in [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32, 40, 48] {
                    add(1i64 << exponent);
                }
                for value in [3, 5, 7, 9, 11, 13, 15, 17, 19, 23, 31] { add(value); }
                // End the closure's borrow so leaf budgets can be checked just
                // as in the C++ for-loop condition before each insertion.
                drop(add);
                for &value in self.targets.iter().rev() {
                    if leaves.len() >= config.n_done { break; }
                    if self.definitions.contains_key(&value) && !blocked.contains(&value) && used.insert(value) {
                        leaves.push(value);
                    }
                }
                let mut intermediates: Vec<(i32, Int)> = self.definitions.keys()
                    .filter(|value| !blocked.contains(value) && !used.contains(value))
                    .map(|&value| (-fanout.get(&value).copied().unwrap_or(0), value)).collect();
                intermediates.sort_unstable();
                if config.seed % 2 != 0 { intermediates.reverse(); }
                for (_, value) in intermediates {
                    if leaves.len() >= config.n_recent { break; }
                    if self.definitions.contains_key(&value) && !blocked.contains(&value) && used.insert(value) {
                        leaves.push(value);
                    }
                }
                dictionary.build(&leaves);
                let mut search = Search::new(&dictionary, config);
                search.run(target);
                if search.end < 0 { continue; }
                let mut trial = self.clone();
                let mut removed_definitions = BTreeMap::new();
                let mut removed_arguments = BTreeMap::new();
                for &value in &blocked {
                    let expression = trial.definitions.remove(&value).ok_or_else(|| "missing blocked value".to_string())?;
                    removed_definitions.insert(value, expression);
                    if let Some(operands) = trial.arguments.remove(&value) { removed_arguments.insert(value, operands); }
                }
                trial.reconstruct(&dictionary, &search, target)?;
                for (value, expression) in removed_definitions {
                    if !trial.definitions.contains_key(&value) {
                        trial.definitions.insert(value, expression);
                        if let Some(&operands) = removed_arguments.get(&value) { trial.arguments.insert(value, operands); }
                    }
                }
                if let Ok(candidate) = trial.compile() {
                    if candidate.len() < best_program.len() {
                        best_program = candidate;
                        *self = trial;
                        improved = true;
                    }
                }
            }
            if !improved { break; }
        }
        Ok(best_program)
    }
}

struct Scanner<R: BufRead> { reader: R, tokens: Vec<String>, position: usize, line: String }
impl<R: BufRead> Scanner<R> {
    fn new(reader: R) -> Self { Self { reader, tokens: Vec::new(), position: 0, line: String::new() } }
    fn next(&mut self) -> Outcome<Option<String>> {
        while self.position >= self.tokens.len() {
            self.line.clear();
            if self.reader.read_line(&mut self.line).map_err(|error| error.to_string())? == 0 { return Ok(None); }
            self.tokens = self.line.split_whitespace().map(str::to_string).collect();
            self.position = 0;
        }
        let token = std::mem::take(&mut self.tokens[self.position]);
        self.position += 1;
        Ok(Some(token))
    }
    fn integer<T: std::str::FromStr>(&mut self) -> Outcome<T> {
        self.next()?.ok_or_else(|| "truncated input".to_string())?.parse().map_err(|_| "invalid integer".into())
    }
}

fn run() -> Outcome<()> {
    let arguments: Vec<String> = env::args().collect();
    fn parameter<T: std::str::FromStr>(arguments: &[String], index: usize, default: T) -> Outcome<T> {
        if let Some(value) = arguments.get(index) { value.parse().map_err(|_| "invalid argument".into()) } else { Ok(default) }
    }
    let config = Config { width: parameter(&arguments, 1, 3)?, alpha: parameter(&arguments, 2, 0.18)?,
        n_done: parameter(&arguments, 3, 52)?, n_recent: parameter(&arguments, 4, 56)?,
        bits: parameter(&arguments, 5, 20)?, seed: parameter(&arguments, 7, 0)?,
        trials: parameter(&arguments, 8, 12)?, passes: parameter(&arguments, 9, 1)? };
    let mode: i32 = parameter(&arguments, 6, 6)?;
    if mode != 6 { return Err("Rust port implements mode 6 only".into()); }
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
        if !(1..=1_000_000_000_000_000_000).contains(&limit) || starts_count == 0 || starts_count > 48 || targets_count > 4096 {
            return Err("invalid puzzle dimensions".into());
        }
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
        let mut puzzle = Puzzle { limit, starts, targets, definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
        let program = puzzle.polish_hint(&hint, &config)?;
        write!(writer, "{} [", id).map_err(|error| error.to_string())?;
        for (index, instruction) in program.iter().enumerate() {
            if index != 0 { write!(writer, ",").map_err(|error| error.to_string())?; }
            write!(writer, "[\"{}\",{},{},{}]", instruction[0] as u8 as char, instruction[1], instruction[2], instruction[3])
                .map_err(|error| error.to_string())?;
        }
        writeln!(writer, "]").map_err(|error| error.to_string())?;
        writer.flush().map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() { eprintln!("Solver error: {}", error); std::process::exit(1); }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mt19937_64_matches_standard_seed_zero() {
        let mut generator = Mt19937_64::new(0);
        assert_eq!(generator.next(), 2947667278772165694);
        assert_eq!(generator.next(), 18301848765998365067);
    }
    #[test]
    fn same_source_arithmetic_and_coverage_are_preserved() {
        let mut puzzle = Puzzle { limit: 9, starts: vec![3], targets: vec![1, 2, 5, 8],
            definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
        let hint = vec![[b'/' as i32,0,0,1],[b'-' as i32,0,1,2],[b'+' as i32,0,2,3],[b'+' as i32,0,3,4]];
        let config = Config { width: 2, alpha: 0.18, n_done: 32, n_recent: 40, bits: 18, seed: 0, trials: 3, passes: 1 };
        let repaired = puzzle.polish_hint(&hint, &config).unwrap();
        assert!(repaired.len() <= hint.len());
        puzzle.load_hint(&repaired).unwrap();
    }
    #[test]
    fn hint_rejects_nonpositive_subtraction_and_inexact_division() {
        let mut puzzle = Puzzle { limit: 100, starts: vec![5, 2], targets: vec![],
            definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
        assert!(puzzle.load_hint(&vec![[b'-' as i32, 1, 0, 2]]).is_err());
        assert!(puzzle.load_hint(&vec![[b'/' as i32, 0, 1, 2]]).is_err());
        puzzle.targets = vec![3];
        puzzle.load_hint(&vec![[b'-' as i32, 0, 1, 2]]).unwrap();
        assert_eq!(puzzle.compile().unwrap(), vec![[b'-' as i32, 0, 1, 2]]);
    }
    #[test]
    fn dictionary_guards_products_near_maximum_limit() {
        let limit = 1_000_000_000_000_000_000;
        let mut dictionary = Dictionary::new(limit, 12);
        dictionary.build(&[limit, 1_000_000_000, 1]);
        assert!(dictionary.expressions.iter().all(|expression| expression.value > 0 && expression.value <= limit));
        let mut puzzle = Puzzle { limit, starts: vec![limit, 1_000_000_000], targets: vec![],
            definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
        assert!(puzzle.load_hint(&vec![[b'*' as i32, 0, 1, 2]]).is_err());
        puzzle.targets = vec![limit];
        puzzle.load_hint(&vec![[b'*' as i32, 1, 1, 2]]).unwrap();
    }
    #[test]
    fn full_seed_bank_retains_legal_overwrite_hint() {
        let mut puzzle = Puzzle { limit: 100, starts: (1..=48).collect(), targets: vec![49],
            definitions: BTreeMap::new(), arguments: BTreeMap::new(), order: Vec::new() };
        let hint = vec![[b'+' as i32, 0, 47, 0]];
        puzzle.load_hint(&hint).unwrap();
        assert!(puzzle.compile().is_err());
        let config = Config { width: 2, alpha: 0.18, n_done: 32, n_recent: 40, bits: 12,
            seed: 0, trials: 0, passes: 1 };
        assert_eq!(puzzle.polish_hint(&hint, &config).unwrap(), hint);
    }
}
