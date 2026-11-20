# Third-party research sources

The compiled dictionary and backward beam-search solver derives from:

- **Tony Li**, [Elf Calculator With Register Reuse](https://www.kaggle.com/code/tonylica/elf-calculator-with-register-reuse).
- **konbu17**, [Santa 2026: Wider Dictionary + Portfolio](https://www.kaggle.com/code/konbu17/santa-2026-wider-dictionary-portfolio-163k), which parameterizes and extends Tony Li's solver.

Both owning notebook pages identify their code as **Apache License 2.0**, verified on 2026-11-18.
The license is included in [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt). Attribution and modification
notices are retained in the derived C++ source and in the Rust (`rust/repair.rs`) and Go
(`go/repair/main.go`) ports of its mode-6 repair engine. These ports retain the dictionary, backward
search, graph repair and 48-register allocator; they do not introduce a new search algorithm.
The Python validation, laboratory, and other experimental
strategies were developed separately in this repository.

The optional public reference programs come from konbu17's
[Santa 2026 Elf Calculator Programs](https://www.kaggle.com/datasets/konbu17/santa-2026-elf-calculator-programs).
The dataset metadata specifies `apache-2.0`. The accompanying `sources.csv` credits the individual methods
and other public contributors selected by that portfolio. Reference programs and their source metadata
remain in the ignored `data/public/` directory; they are used for comparison and as an explicitly identified
fallback. Results obtained by merging public programs are reported separately from programs constructed
by this repository's experiments.

The language comparison starts from the best locally validated portfolio, seeded by
[Hanif's public peephole programs, version 4](https://www.kaggle.com/datasets/hanifnoerrofiq/santa-2026-elf-calculator-peephole-programs/versions/4),
whose saved dataset metadata specifies `apache-2.0`. The frozen reference has 144,248 instructions;
the comparison baseline has 144,238 after incorporating local improvements. Those inherited programs
are not attributed to the language ports. Dataset metadata, validation reports and CSV snapshots remain
in the ignored local data/output directories.

Later Rust quality experiments use the public programs from **Atakan Aldemir**,
[Santa 2026 Joint Recipes for Large Puzzles, version 2](https://www.kaggle.com/code/atakanaldemir/santa-2026-joint-recipes-for-large-puzzles?scriptVersionId=371115522).
The owning notebook explicitly identifies its license as Apache 2.0, verified on 2026-11-18.
Its 1,000 programs replay with 142,272 instructions and SHA-256
`145703a2287ef24eb3bc141708538cd8ba013a5d43f5126a2f3f1af9bdc592fc`.
The pinned notebook, original upstream attributions, source bundle, license evidence and validation
are preserved locally under `data/public/research/reference-atakan-v2/`. This is an inherited public
reference, not an improvement produced by the Rust experiments. The Rust joint-recipe search uses
coordinated graph mutations, whereas the notebook's published joint selection uses CP-SAT.

The newer [Atakan notebook version 8](https://www.kaggle.com/code/atakanaldemir/santa-2026-joint-recipes-for-large-puzzles?scriptVersionId=371150866)
was separately pinned and its owning page again verified as Apache 2.0. Its 1,000 programs replay
with 141,394 instructions and SHA-256
`435d97be3423ba2369926f1bfd38bd2b0d958f605a24816a718cf130764887d5`.
The separate snapshot and evidence are under `data/public/research/reference-atakan-v8/`.
This is also a public-reference result, not a gain attributed to this repository's Rust searches.

The fresh constructor in `rust/construct.rs` is independently implemented in Rust using resident-bank
joins, residual planning and scale/anchor retention inspired by **Pablo Diego's**
[Deterministic Atlas Solver, version 1](https://www.kaggle.com/code/pablodiegoo/santa-2026-deterministic-atlas-solver?scriptVersionId=371078087).
Its owning page identifies the code as Apache 2.0, verified on 2026-11-18; pinned source, original
credits and license evidence remain in `data/public/research/atlas-solver/`. Downloaded foreign code
was inspected but not executed. The constructor retains the Tony Li/konbu17 attribution for reused
dictionary and expression primitives.

On 2026-11-21, the public frontier was refreshed from **goodpjw2008**:

- [LNS + CP-SAT Recombine, notebook version 50](https://www.kaggle.com/code/goodpjw2008/santa-2026-lns-cp-sat-recombine-lb-132-552?scriptVersionId=372225385),
  132,552 instructions, CSV SHA-256 `a93780bac76cbcabe1e21cb7fbc07e884ddb4f254ed82205e688e9ba4825ab47`.
- [DAG-LNS programs, dataset version 50](https://www.kaggle.com/datasets/goodpjw2008/santa-2026-dag-lns-programs/versions/50),
  132,543 instructions, `our_programs.csv` SHA-256 `e453812a519ce1ed6e1471e0e6e572cf2c4a85934c57080f6b9f9a1b46943a80`.

Both owning pages and the official dataset metadata specify Apache 2.0. All
1,000 programs in each snapshot passed this repository's exact validator.
The notebook credits Ishan Karnick, Mizero Lucky Gall, E-Max AI, Alex Chilton,
Sargis Khachatryan, Wang Jakie, Roman Rozen, Santiago Zazo, Hanif Noer Rofiq,
Atakan Aldemir, Hansen0223, prvsiyan, Tony Li, konbu17 and their upstream
contributors. Full original credits, license evidence, frozen source and
validation reports remain under the ignored `data/public/research/` snapshots.
These frontier improvements are inherited public results, separate from any
measured improvement produced by local Rust experiments.

`rust/lns.rs` adapts the Apache-2.0 value-DAG search in goodpjw2008's
[`elfopt.cpp`, dataset version 49](https://www.kaggle.com/datasets/goodpjw2008/santa-2026-dag-lns-programs/versions/49)
(SHA-256 `16f12d4963acfe795f89f9632e941fb1aea9d8d8de55ac958a45b17a02881db2`)
into Rust. That source explicitly credits prvsiyan's value-DAG neighborhood
design. The local implementation also reuses the attributed primitives and
allocator from `rust/repair.rs`. Downloaded foreign code was inspected and
archived, never executed. The Rust translation retains the license and credits.

The 2026-11-21 research refresh also inspected the Apache-2.0
[Goodpjw dataset version 55](https://www.kaggle.com/datasets/goodpjw2008/santa-2026-dag-lns-programs/versions/55).
Its 1,000 programs replay with 132,516 instructions; the source CSV SHA-256 is
`6b1b73875bab6a73aa676b6808f8a3e90076662c88e529ff000b1a4ab0cf6688`.
Merging it with the existing local one-instruction Rust improvement gives
132,515. That additional public-reference saving is not attributed to the new
Rust research. Frozen license evidence, original attributions and validation
are under `data/public/research/reference-goodpjw-dataset-v55/`.

The experimental atomic frontier reconstruction in `rust/lns.rs` is developed
locally using the existing search primitives. Its shared-cost rationale is
informed by the primary papers cited in `docs/research/`, and by Atakan
Aldemir's credited interface/block-reconstruction work inspected through the
Apache-2.0 source snapshots. Published gains on older public baselines are
reported as upstream measurements, not local results.
