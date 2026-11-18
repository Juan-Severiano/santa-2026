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
