# Mode 6 repair engine in Rust

`repair.rs` is a standard-library-only port of the frozen C++ mode 6 engine.
It preserves dictionary insertion order, backward beam search, hint DAG repair,
the 48-register allocator, and Apple libc++'s MT19937-64 shuffle schedule.
It emits and flushes one result per puzzle. Other solver modes are unsupported.

Build from the repository root:

```sh
rustc --edition=2021 -O -C target-cpu=native rust/repair.rs -o outputs/bin/repair-rust
```

The positional arguments are `width alpha n_done n_recent index_bits 6 seed
trials passes`. Input and output use the same protocol as the C++ repair engine.
Use the shared language comparison harness for performance measurements;
instruction counts and validity must match before comparing speed.

Run the Rust boundary checks with:

```sh
rustc --edition=2021 --test -O rust/repair.rs -o outputs/bin/repair-rust-tests
outputs/bin/repair-rust-tests
```

The solver is derived from Tony Li's calculator solver and konbu17's
parameterized portfolio under Apache-2.0. See the source attribution,
`THIRD_PARTY.md`, and `licenses/Apache-2.0.txt`.

## Joint tail optimizer

`optimizer.rs` includes the unchanged repair engine and adds a separate search.
It removes the exclusive derivations of the last 6–10 completed targets, keeps
the earlier targets' dependency graph, and reconstructs the tail. A beam of
three distinct graph/schedule states evaluates up to six next-target choices
plus alternative derivations. The rank is the compiled prefix cost plus 0.4
times the sum of the additional compiled costs of each remaining target.
Only shorter complete programs that pass strict replay can replace the input.

Build it with `rustc --edition=2021 -O -C target-cpu=native rust/optimizer.rs -o
outputs/bin/optimizer-rust`. It uses the same arguments and mode-6 hint protocol.
`trials` controls tail neighborhoods and `passes` controls repeated passes.
Search also stops after at most `512 * max(1, trials) * passes` beam searches
(capped at 100,000), or its per-puzzle time budget. The default time budget is
`400 * max(1, trials) * passes` milliseconds, clamped to 1–30 seconds. Set
`SANTA2026_OPTIMIZER_MS` to an explicit 1–600,000 milliseconds to override it.
Cancellation is checked between backward-beam parent expansions; dictionary
construction and a single expansion can slightly exceed that soft deadline.
`SANTA2026_OPTIMIZER_TRACE=1` writes search counts and timing to stderr.

For short budgets, an initial greedy completion supplies an incumbent before
the more expensive graph beam, and the last part of the budget is reserved for
finishing the best partial state. The original hint is retained on timeout or
when allocation cannot fit. The optimizer tests run with `rustc --test -O
rust/optimizer.rs -o outputs/bin/optimizer-rust-tests` followed by that binary.

Set `SANTA2026_OPTIMIZER_BOUNDARY=1` to enable the optional boundary-first
variant. It computes the exact original edges entering the removed tail from
the retained prefix. Those prefix values receive leaf priority after every
starting value and before the fixed palette or completed targets. In this
variant all additional leaves respect `n_recent`; starting values are always
kept even if their count exceeds that cap. Leaf-order perturbations preserve
the priority prefix. A paid branch can also reconstruct each target through
its original removed recipes, and lookahead compares that branch with the
fresh search result. Retained values remain available; removed helpers pay
their actual construction cost. Duplicate graph/schedule candidates are
removed before the shortlist. The default, unset or `0`, keeps V1 selection
and search choices. Other flag values are rejected.

## Puzzle-only constructor

`construct.rs` generates a complete program directly from starting values and
targets. It never reads a hint or an existing submission. Its live 48-register
bank pays every bootstrap, temporary and target operation. Exact one/two-step
tables and inverse joins find plans through four operations; otherwise it
constructs a nearby product or sum and recursively resolves the residual,
ranking candidates by the bits removed per instruction. Short target choices
use one-step lookahead against the resulting resident bank. Register eviction
preserves scale diversity and anchors near remaining targets, and a backward
pass removes instructions not needed for target coverage.

Build: `rustc --edition=2021 -O -C target-cpu=native rust/construct.rs -o
outputs/bin/construct-rust`. Arguments are `seed effort milliseconds`, default
`0 1 2000`; effort is 1–8. Input is `id limit ns nt starts... targets...` with
no hint. Output is the same one-line JSON program protocol, flushed per puzzle.
The time limit is a soft planning budget: after it expires the constructor
finishes missing targets with binary arithmetic before emitting a complete
program. Set `SANTA2026_CONSTRUCT_TRACE=1` for per-puzzle timing on stderr.
Tests: `rustc --edition=2021 --test -O rust/construct.rs -o outputs/bin/construct-rust-tests`.

The construction strategy is inspired by Pablo Diego's Apache-2.0
[Deterministic Atlas Solver](https://www.kaggle.com/code/pablodiegoo/santa-2026-deterministic-atlas-solver?scriptVersionId=371078087).
The implementation reuses the attributed integer index from `repair.rs` and
implements its own physical-bank execution. Its standalone score and any
recipe-pool benefit must be measured separately against the validated best
submission; a diverse construction is not automatically a score improvement.

Constructor seeds use all 64 bits for leaf insertion, interleaved P2 phases,
target and recipe ties, and an optional odd helper. Seed zero keeps the original
ordering and palette profile. Seeds are no longer limited to twelve repeated
profiles. Approximate joins also retain exact zero-residual results, count
shared temporary values at their real emitted cost, and examine the product
neighbor above an exact floor quotient. A value reached by several plans keeps
the cheapest actual plan. In particular a shared P2+P2 diamond can cost four
operations rather than its five-operation tree expansion.

## Whole-graph neighborhood search

`lns.rs` adapts goodpjw2008's Apache-2.0 value-DAG optimizer, with the original
prvsiyan design credit. It removes the private cone of a target or shared
intermediate, excludes dependent descendants from the external context, and
rebuilds against every remaining graph value. Arithmetic inversions include
exact final divisions whose numerator exceeds the target. One-step tables,
three-operation joins, bounded four-operation joins and five-operation
near-miss products propose replacements. This is a bounded heuristic, with no
global optimality guarantee: four-operation search aborts above 750,000 needed
values, and the product search examines four neighbors near the quotient.

Equal-cost moves and perturbations explore different graphs while retaining
the best program separately. Identical reconstruction of the current root is
skipped, and shared temporary operations are charged once. Topological
scheduling prioritizes the original instruction order, releases dead starting
values, enforces 48 real registers and replays the complete candidate.

```sh
python3 scripts/build_native.py --engine rust --source rust/lns.rs --output outputs/bin/lns-rust
rustc --edition=2021 --test -O rust/lns.rs -o outputs/bin/lns-tests
outputs/bin/lns-tests
```

Arguments: `seed milliseconds flags`, defaults `0 2000 15`. The budget is
1–600,000 milliseconds. Feature bits are 1 for helper roots, 2 for equal-cost
moves, 4 for depth-five products, and 8 for perturbations. It uses the existing
single-hint input and one-line `id JSON-program` output protocol. Search checks
a soft deadline; the Python controller enforces the outer wall-time limit.

`SANTA2026_LNS_ARCHIVE=/path/archive.json` optionally writes up to eight
replay-valid, recipe-diverse trajectory programs, each at most best+2
instructions. The sidecar is atomic and stdout still contains only the best
program. `scripts/rust_quality.py --methods lns --lns-archive` validates and
exports these alternatives as complete `lns-generated-N.csv` portfolios;
their scores are reported separately from best-score improvements.
`scripts/lab.py --strategies recombinecross --candidates ...` recombines their
observed values, adds legal arithmetic edges, and enforces acyclic selection
with optional OR-Tools CP-SAT. The search itself remains Rust; Python handles
orchestration, model assembly and independent exact validation.
