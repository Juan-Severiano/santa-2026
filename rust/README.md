# Mode 6 repair engine in Rust

`repair.rs` is a standard-library-only port of the frozen C++ mode 6 engine.
It preserves dictionary insertion order, backward beam search, hint DAG repair,
the 48-register allocator, and Apple libc++'s MT19937-64 shuffle schedule.
It emits and flushes one result per puzzle. Other solver modes are unsupported.

Build from the repository root:

```sh
rustc -O -C target-cpu=native rust/repair.rs -o outputs/bin/repair-rust
```

The positional arguments are `width alpha n_done n_recent index_bits 6 seed
trials passes`. Input and output use the same protocol as the C++ repair engine.
Use the shared language comparison harness for performance measurements;
instruction counts and validity must match before comparing speed.

Run the Rust boundary checks with:

```sh
rustc --test -O rust/repair.rs -o outputs/bin/repair-rust-tests
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

Build it with `rustc -O -C target-cpu=native rust/optimizer.rs -o
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
