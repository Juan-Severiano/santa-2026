# Third-party research sources

The compiled dictionary and backward beam-search solver derives from:

- **Tony Li**, [Elf Calculator With Register Reuse](https://www.kaggle.com/code/tonylica/elf-calculator-with-register-reuse).
- **konbu17**, [Santa 2026: Wider Dictionary + Portfolio](https://www.kaggle.com/code/konbu17/santa-2026-wider-dictionary-portfolio-163k), which parameterizes and extends Tony Li's solver.

Both owning notebook pages identify their code as **Apache License 2.0**, verified on 2026-11-18.
The license is included in [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt). Attribution and modification
notices are retained in the derived C++ source. The Python validation, laboratory, and other experimental
strategies were developed separately in this repository.

The optional public reference programs come from konbu17's
[Santa 2026 Elf Calculator Programs](https://www.kaggle.com/datasets/konbu17/santa-2026-elf-calculator-programs).
The dataset metadata specifies `apache-2.0`. The accompanying `sources.csv` credits the individual methods
and other public contributors selected by that portfolio. Reference programs and their source metadata
remain in the ignored `data/public/` directory; they are used for comparison and as an explicitly identified
fallback. Results obtained by merging public programs are reported separately from programs constructed
by this repository's experiments.
