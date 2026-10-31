# forge

[SlopCodeBench](https://github.com/gabeorlanski/scb-problems)'s `forge` is a command-line app of about 1,800 lines of Python, for hardware blueprints, allocations, units and modules. It is the application the companion was first evaluated on. It is a use of the tool, not part of it: nothing in `companion/` or `lean/` depends on this directory.

The companion normally writes an application's model and adapter itself (see the top-level README). This directory holds hand-built ones, in the same layout as a generated workspace. They serve as:

- the reference model and properties for the evaluation (`docs/evaluation.md` §4);
- the model behind the feasibility study (`studies/feasibility/`);
- a stand-in for an LLM-written model in the tests (`tests/`).

| Path | What it is |
| --- | --- |
| `adapter.py` | Runs abstract actions on the real program and reads its state back in the model's terms |
| `lean/` | Lake project, which requires the core in the top-level `lean/` |
| `lean/Forge/` | `Model`, `Props`, `Proofs`, `Variants` (models that follow seeded bugs) and `App` (wiring). `Main` builds `forge-model` (hand-written properties). `CompanionMain` builds `forge-companion` (properties from the ledger, generated into `Forge/Ledger.lean`). |
| `companion.json` | The companion's fixed config for this model |
| `example-ledger.json` | A constraint ledger for a sample session |

## Running

The program comes from the benchmark, which is not in git:

```
studies/feasibility/fetch_data.sh
(cd apps/forge/lean && lake build)      # also checks the proofs
```

The model has a fixed config, so `check` cannot update it; it only reports that it no longer matches the program:

```
companion --workspace apps/forge/companion.json reset --from apps/forge/example-ledger.json
companion --workspace apps/forge/companion.json check    # --program PATH checks another version
```

The first `check` takes about 20 s (125 CPU-seconds, 6 workers). Later checks rerun only the traces whose program lines changed: about 3 s after an edit, 2 s with no change. Session state, including that cache, goes to `.companion/forge/` at the repository root (not in git).
