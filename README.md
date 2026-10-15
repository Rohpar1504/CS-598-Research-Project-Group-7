# CS-598-Research-Project-Group-7

Code for the CS 598 research project of Group 7 (Rohan and Jamie): a Lean companion for vibe coding. Start with the [project brief](Live%20Lean%20Companion%20for%20Vibe%20Coding%20Project%20Brief.md).

## Layout

```
docs/                     design, evaluation plan, and the studies so far
lean/                     Lake project (Lean 4.34.1, no Mathlib)
  Companion/Core.lean     generic: model interface, replay, breadth-first search, trace selection
  Companion/Cli.lean      generic JSON command line for an app's model
  Forge/                  model of SlopCodeBench's forge app: Model, Props, Proofs, Variants
                          (models that follow seeded bugs), App (wiring); Main builds forge-model
                          (study, hand-written properties), CompanionMain builds forge-companion
                          (properties from the ledger, generated into Forge/Ledger.lean)
companion/                the companion (Python)
  __main__.py             command line: new, synthesize, validate, init, ledger, build, check,
                          extract, hook
  workspace.py            an application's workspace: the LLM-written model and adapter
  sync.py                 keeping the model in step with the code: synthesis, validation, repair
  coverage.py             changed code that no validation trace reaches
  prompts/                the agent's tasks: write the model, repair it
  ledger.py               the constraint ledger, rendered into Lean and typechecked
  extract.py              developer message -> ledger changes, through an LLM
  check.py                sync, search, replay on the code, evidence per constraint
  hooks.py                Claude Code hooks (UserPromptSubmit, Stop)
  llm.py                  LLM backends: Claude Code headless (completions, and an agent with
                          tools), or fixtures for tests
  trace_validation.py     runs traces on the real app and the model and compares them: shared
                          prefixes, observing only where the model's state changes
  validation_cache.py     reuses a trace's real results until code they ran is changed
  runner.py               runs the program under test: by fork of a pre-loaded copy for Python
                          programs (about 10x cheaper than a new process), recording which
                          lines each run executes; a new process otherwise
  abstraction.py          how an adapter marks output the model's types cannot express
  config.py, lean.py, adapters/forge.py
apps/forge/               the companion's config for forge, an example ledger, hook settings
tests/                    what the companion must do, checked on the real forge program
studies/
  preliminary/            regressions in EvoCode-Bench's published results
  feasibility/            the forge prototype's experiments; results/ holds their output
  synthesis/              the standalone run of an LLM writing forge's model, and its grader
data/                     downloaded benchmarks and generated files (not in git)
```

## Running

Requires Lean through elan, and Python 3.12 or later (no other Python packages). Either run
from the repository root, or install with `pip install -e .`, which also provides a `companion`
command.

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

## Tests

```
python3 -m unittest tests.test_runner        # each file takes from seconds to a few minutes
python3 -m unittest discover tests            # all of them (about 15 minutes)
```

The tests state what the companion must do and check it on the real forge program: the
runner behaves like a new process; validation flags the same traces however much work it
skips; reusing cached results never changes the outcome after an edit; the ledger, extraction
(with a stand-in for the LLM), `check` and the hooks. They need the benchmark data and the
Lean build, and are skipped otherwise. They use two worker processes and keep temporary files
in `data/tmp/`, because many workers forking at once can overload a machine.

## The companion (prototype)

Each application gets a workspace, where an LLM agent (Claude Code, headless) writes a Lean
model of the program and an adapter, and keeps them in step with the code:

```
python3 -m companion new WS --program path/to/app/forge     # a workspace, no model yet
C="python3 -m companion --config WS"
$C synthesize                       # the agent writes the model and adapter (minutes)
$C init                             # start a session with an empty ledger
$C extract "a bind should be all or nothing"
$C check                            # repairs the model first if the code has moved on
$C ledger
```

The agent's model and effort are set in `WS/companion.json` (`agent`). After every turn,
`check` validates the model against the code; if they disagree, or the code changed where no
validation trace reaches, the agent repairs the model (at most `repair_rounds` rounds) before
constraints are checked. Each constraint also records the situation it is about (when it could
be broken); a constraint is only reported checked if the search reaches that situation, and if
it does not, the agent widens the search areas. Hook settings for Claude Code are in `apps/forge/claude-settings.json`.

The hand-built forge model has its own fixed config, which `check` cannot repair (it only
reports that the model no longer matches):

```
C="python3 -m companion --config apps/forge/companion.json"
$C init --from apps/forge/example-ledger.json
$C check                                           # add --program PATH to check another version
```

With the default settings in `apps/forge/companion.json`, the first `check` of a session takes
about 20 s (125 CPU-seconds, 6 workers). Later checks rerun only the traces whose program lines
changed: about 3 s after an edit, 2 s with no change. Session state, including that cache, goes
to `.companion/` (not in git). Not built yet: triage of changes of mind, confirmation
questions, and proofs per constraint.
