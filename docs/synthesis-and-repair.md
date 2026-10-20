# LLM-written models: synthesis and repair

Can an LLM write the Lean model and the adapter of an application from its code alone, and keep them in step with the code as a coding agent changes it? This was the largest open question after the feasibility study ([feasibility-study.md](feasibility-study.md)), where the model and adapter were written in conversation with the spec and code at hand.

The target is again SlopCodeBench's forge app (checkpoint 8, about 1,800 lines of Python). The LLM is Claude Haiku 5.5 at low effort, running as Claude Code headless with file and shell tools. Haiku is the weakest model we use, so these results are a lower bound.

## How the tool does it

Each application gets a **workspace** (`companion new`). The model and the adapter live there, together with the generic Lean core and a validation script. The LLM agent works inside the workspace: it reads the program's code, writes the model and adapter, and runs trace validation itself until the model matches.

- **Synthesis** (`companion synthesize`). The task is in [companion/prompts/synthesize.md](../companion/prompts/synthesize.md). It asks for:
  - a model that follows the code, including its order of error checks;
  - search areas;
  - a wider alphabet for random validation traces.

  The task is done when validation reports no mismatching traces. The agent is told to model what the code does even where the code looks wrong: the developer's intent lives in the constraints, not in the model.
- **Repair**, after every agent turn (`companion check`, also run by the `Stop` hook). The agent is asked to update the model and adapter in three cases:
  - **Validation mismatches.** The model and the code disagree on some trace.
  - **Unreached changed code.** Code changed since the model last matched, and no validation trace executes it. A new command is the typical case: trace validation cannot see what no trace reaches, so it would keep passing.
  - **Unreached situations.** A constraint's situation is never reached by the search (see below).

  The repair task ([companion/prompts/repair.md](../companion/prompts/repair.md)) contains:
  - the code's diff since the model last matched;
  - the disagreeing traces, with expected and real outputs;
  - the unreached statements;
  - the constraints, which the agent must not rename out from under.

  After a repair, constraints whose predicates no longer typecheck get one LLM repair round. Constraints that could not be formalized before are retried, since the model may now have the names they need. A repair that leaves a model that does not build is undone. If repair fails, nothing is claimed checked.
- **Situations.** Each constraint records the situation it is about: the circumstance in which it could be broken, as a sentence and as a Lean predicate. A constraint is reported checked only if the search reaches its situation. This closes a hole the first live run exposed (below). The design called this the vacuity check.

## Results

### An earlier standalone run

Before the tool had a `synthesize` command, a standalone script (`studies/synthesis/`) gave Haiku the same kind of task in an empty directory. It finished in 9 minutes for $0.29. The result was graded independently of the agent's own validation (`studies/synthesis/grade.py`):

- **Matches the program.** 0 mismatching traces out of 3,181 (16,934 steps), using more and different traces than the agent saw.
- **Covers almost everything.** 28 of forge's 29 commands are modeled; only `version` is missing.
- **Notices most seeded bugs.** 7 of the 10 seeded bugs make the model mismatch the buggy code. The three it misses are all in features its model leaves out: partial name deletion, system scope, and tenant visibility by revision.
- **Small.** 425 lines of model and 301 of adapter, against about 600 and 350 for the hand-built ones.

### The tool, live (`data/live/`, not in git)

1. **Synthesis.**
   - The model and adapter match the program on all 995 validation traces, with no repair rounds needed. This took 30 minutes and cost $0.67.
   - The model has six search areas and a 122-action validation alphabet.
   - Left out, by the agent's own account: roles and rules files, the `latest` revision, and malformed command lines.
2. **Three constraints, extracted by the LLM** from developer messages against the new model's vocabulary:
   - c1: binding to an assignment that already has an allocation bound is refused;
   - c2: an allocation counts as bound exactly when it has an assignment;
   - c3: a blueprint in use cannot be deleted.

   Forge itself breaks c3.
3. **The coding agent adds a command**, `allocation release`, with a bug: it unbinds allocations but leaves their assignment set.
   - Validation still passed, since no trace used the new command, but 10 changed statements were unreached. The companion called the agent.
   - The agent extended the model, adapter and areas in 83 s for $0.03, following the bug.
   - Search then found c1 broken through `release`, and the violation reproduced on the real program.
4. **The coding agent drops the duplicate-assignment check** from `bind`.
   - 14 traces disagreed. The agent removed the matching check from the model in 19 s for $0.01.
   - c1 was reported broken, with a reproduction.
5. **The hole: c2 and c3 were reported checked although the code breaks both.**
   - No search area combined allocation creation with blueprint deletion, or the `release` command with listing by status. The search never reached the situations these constraints are about.
   - Nothing in the tool noticed. "No violation found" was reported as "the rule holds", when the search had never tried.
   - This was a defect of the tool, not of the LLM. Any LLM-written model can choose areas like these, and the hand-built model has the same exposure for other constraints.
6. **With situations.**
   - The constraints were extracted again, and the extractor gave each a sensible situation. c3's is "a delete targets a blueprint that an existing allocation was created from".
   - The search reached c1's situation but not c2's or c3's. The agent was asked to widen the areas, and did so in 66 s for $0.02.
   - All three constraints are now reported violated, and each violation reproduces on the real program.

## What this shows, and what it does not

- **An LLM can write the model and adapter for an application of this size from its code alone,** and keep them in step through edits. Each repair took a minute or two and a few cents. Synthesis took 9–30 minutes and under $1.
- **Following the code works as intended.** Both times, the agent modeled the new bug and said in its report that a developer's rule would now fail. The constraint check then found the bug.
- **"Checked" needs a reached situation.** Without it, the evidence level overstates what search established. With it, the claim's strength depends on how specific the extracted situations are. A vague situation is reached easily and would not catch the gap, so the quality of situations is part of RQ1.
- **Not yet shown:**
  - other applications, including a non-Python one;
  - stronger models than Haiku;
  - repeated runs, to measure variance;
  - repairs of agent-written edits, as opposed to our seeded ones;
  - how often a stale or narrow model still slips through (tracked as a check on the tool; evaluation.md §9).

  The live run's synthesized model was checked only against the tool's own validation traces, not graded independently like the standalone run.

## Reproducing

```
cd PATH/TO/forge-copy
companion init forge --app forge
# set "agent": {"model": "claude-haiku-5-5", "effort": "low"} in .companion/companion.json
companion synthesize
companion tell "if an assignment already has something bound, binding more to it has to be refused"
companion tell "an allocation should count as bound exactly when it has an assignment"
companion tell "don't let a blueprint be deleted while allocations still use it"
companion check                            # after each edit to the program
```

The standalone run: `python -m studies.synthesis.run_synthesis --workspace DIR --model claude-haiku-5-5 --effort low`, then `python -m studies.synthesis.grade DIR`. The tool's behavior is specified by `tests/test_sync.py`, which uses a stand-in for the LLM agent.
