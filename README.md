# CS-598-Research-Project-Group-7

Code for the CS 598 research project of Group 7 (Rohan and Jamie): a Lean companion for vibe coding. Start with the [project brief](Live%20Lean%20Companion%20for%20Vibe%20Coding%20Project%20Brief.md).

## Layout

```
docs/                     design, evaluation plan, and the two studies so far
lean/                     Lake project (Lean 4.34.1, no Mathlib)
  Companion/Core.lean     generic: model interface, replay, breadth-first search, trace selection
  Forge/                  model of SlopCodeBench's forge app: Model, Props, Proofs,
                          Variants (models that follow seeded bugs), Main (JSON command line)
companion/                Python side
  lean.py                 calls the Lean executable
  trace_validation.py     runs traces on the real app and on the model, and compares them
  adapters/forge.py       drives the forge app through its command line
studies/
  preliminary/            regressions in EvoCode-Bench's published results
  feasibility/            the forge prototype's experiments; results/ holds their output
data/                     downloaded benchmarks and generated files (not in git)
```

## Running

Requires Lean through elan, and Python 3.11 or later. No Python packages are needed.

```
studies/preliminary/fetch_data.sh && studies/feasibility/fetch_data.sh   # benchmarks into data/
(cd lean && lake build)                                                  # also checks the proofs

python3 studies/preliminary/extract_regressions.py                       # preliminary study
python3 studies/preliminary/summarize.py

python3 -m studies.feasibility.make_mutants                              # forge prototype
python3 -m studies.feasibility.validate --checkpoints 8
python3 -m studies.feasibility.search_and_confirm
```

[docs/feasibility-study.md](docs/feasibility-study.md) lists every experiment command.
