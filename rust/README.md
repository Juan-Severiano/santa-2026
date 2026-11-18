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
