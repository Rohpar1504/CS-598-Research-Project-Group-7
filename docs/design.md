# Lean Companion: Design

Oct 13, 2026. The design is settled. This document is the reference for how the tool works. Where the project stands, and what comes next, is in the project brief.

Decided:

- We do not translate Python. Strata-Python already does that, and its subset rejects the code agents write (Appendix A).
- The model is checked against the code by refinement testing from outside the application.
- The companion runs once per agent turn.
- The companion does not depend on the application's language.

The evaluation is in [evaluation.md](evaluation.md). The forge prototype that tested this design is in [feasibility-study.md](feasibility-study.md), and the LLM-written models in [synthesis-and-repair.md](synthesis-and-repair.md).

## 1. Goal and scope

### The claim

> After each agent turn, the companion checks a Lean model that follows the code against the constraints stated in the conversation. It reports only violations it has reproduced on the real code. It tells regressions apart from changes of mind. For every agreed constraint, it states how strong the evidence is that the code keeps it.

### What the developer gets

1. **A constraint ledger.** It lists every rule agreed in the conversation, with where it was said and its current status. It is a specification that emerges from the session, and the developer never has to write it.
2. **Evidence for each constraint**, refreshed every turn. The levels are proved, checked to depth k, violated (with a reproduction), not observable, or not checked. This answers the question in the proposal's abstract: how can we gain confidence that vibe-coded software does what the developer asked for?
3. **Fewer silent regressions.** A violation reproduced on the real code goes back to the agent with a concrete reproduction. It is kept as a regression test afterwards.
4. **Questions only when they matter.** The companion asks the developer something only when a constraint is uncertain, or when a new request seems to override an earlier one (§5). Each question comes with a concrete scenario.

### What Lean adds

Three things that testing the real code cannot give:

- an exhaustive search over a fast abstract model;
- proofs that hold for action sequences of any length;
- a formal object in which conflicts between constraints show up as concrete scenarios.

If property-based tests (PBT) built from the same constraints detect just as many bugs (RQ2), the companion still provides items 1, 2 and 4. The evaluation is designed so that a tie in detection is still a result (evaluation.md §3).

### What is in scope

Any constraint the developer states that can be decided from what the application shows through its interface. That covers its state, the outputs of its actions, and which inputs it accepts or rejects. Examples:

- **State invariants.** The ledger equals the union of capabilities over active units.
- **Transition properties.** `rev` increases by 1 on every change. A rejected request leaves the state unchanged. After `DELETE k`, the next `GET k` returns NOT_FOUND.
- **Input handling.** A request with an empty `blueprint_name` is rejected with `validation_error`. Errors are checked in a stated priority order.
- **Output properties.** List output is sorted by `created_at`, then by `uuid`. Every failure returns a JSON object with an `error` field. Timestamps are ISO 8601 in UTC.
- **Input-output behavior of commands that keep no state.** A query returns only rows that match its WHERE clause. Converting a file twice gives the same result as converting it once.

What is out of scope:

- requirements nobody stated, because the companion does not invent requirements;
- performance;
- visual appearance;
- behavior that depends on outside services the adapter cannot control.

All of these are checked in Lean, in the same way. A constraint about outputs is a predicate on the outputs the model's actions return, so the model's outputs carry whatever detail the stated constraints mention: a format, an ordering, an error code. Trace validation keeps those outputs matching the real ones. The model therefore grows only as far as the stated constraints require.

## 2. Concepts

| Term | Definition |
| --- | --- |
| **Observation interface** | The read actions the application offers, such as `forge unit list`, `GET /v1/configs/x` or `SCAN`. Everything the companion knows about the real state comes through these. |
| **Schema** | A Lean type for the observable state of the application, over small finite domains. It comes from the conversation and the spec (the nouns the developer uses) and changes rarely. |
| **Model** | `step : State → Action → State × Out`, written by an LLM, following what the code does. A rejected request is an output (an error), not a failure of the model, because many constraints are about which requests are rejected. |
| **Adapter** (the report's "test harness") | γ maps an abstract action to a real call. Abstract input classes, such as `valid`, `emptyName` or `wrongType`, map to concrete payloads. α maps the real state to a `State` by calling the observation interface, and maps real outputs to `Out`. The adapter keeps a map between abstract IDs and real IDs. |
| **Property** | A decidable Lean predicate over an observation. The observation can be a `State`, a transition `(State, Action, Out, State)`. Because the predicate is decidable, it can be evaluated on the model and on observed real runs alike. |
| **Ledger entry** | id, text, predicate, kind, provenance (quotes and turn numbers), status (proposed, confirmed, superseded or retired), confidence, assurance level, and its **situation**: the circumstance in which it could be broken (for "when X, then Y", it is X), as text and as a predicate on transitions. |
| **Assurance level** | `proved`: proved preserved by every action, and the model passes trace validation. `bounded(k)`: no violation within depth k. `violated`: a reproduced trace exists. `unobservable`: α cannot see the state the property refers to. `unchecked`: the model is broken, the time budget ran out, or the search never reaches the entry's situation. |

Why the model follows the code while the properties follow the developer. The FuzzMo paper calls these code-based and ideal-based models. A conversation leaves most behavior unspecified. An ideal model would have to fill in those details, and every design choice the agent made would then show up as a mismatch. A model that follows the code absorbs those choices. Only the properties the developer actually stated are held fixed. A violation therefore means the code does something the developer said it must not.

## 3. Assurance argument

When the companion says "constraint C is proved", that rests on the following chain:

1. **Proof.** Lean has checked that every action of model M preserves predicate P. The trusted parts are the Lean kernel, plus the claim that P correctly formalizes C (RQ1).
2. **Trace validation.** M agrees with the code on every trace in the validation set T, up to α and γ. This is tested, not proved, so how T is chosen matters. In the prototype, random traces missed 4 of 10 seeded bugs, and transition coverage caught all of them (§4).
3. **Coverage.** T exercises x% of the model's branches and y% of the code lines reached by actions. Measured with the language's coverage tool.

The parts we trust are α and γ (small, generated and reviewed), the adapter's runtime, and the Lean kernel. The leftover risk has two measurable parts:

- **Coverage gaps.** Code that T never reached. The guarantee does not extend there, and the report says so.
- **Escapes.** A property marked proved or bounded that is in fact violated on the code. The evaluation records the escape rate against an independent oracle (evaluation.md §9). This measured rate replaces the guarantee by construction that translation would have given.

## 4. The per-turn pipeline

```
developer message ──► [1 ledger update] ──────────────────────────────┐
                                                                      ▼
agent turn ends ──► [2 change impact] ──► [3 trace validation + repair] ──► [4 check + prove]
                                                                      │
                     ┌──────────────── violations ────────────────────┘
                     ▼
              [5 replay on code] ──► [6 triage] ──► agent  (regression)
                                              └──► developer (possible change of mind)
                                              └──► model repair (model error)
```

1. **Ledger update**, on each developer message. Add, revise or retire entries, with provenance. If confidence is low, put a confirmation question in a queue (§8).
2. **Change impact.** Find what this turn's diff affects. Each validation trace's code footprint is recorded during trace validation, and a trace is rerun only when the diff touches a function it executed. Changed statements that no trace executes, such as a new command, mean the model may not cover the new behavior: they go to repair in step 3 even if validation passes.
3. **Trace validation.** Traces are chosen by **transition coverage**: a shortest path to every reachable state in the search scope, extended by every action. Random longer traces and earlier traces on which the model was wrong are added. On a mismatch, or on unreached changed code, the LLM repairs the model or the adapter, then validation runs again. The budget is N rounds. If the model still does not conform, every property becomes `unchecked`.
4. **Check and prove.**
   - BFS to depth k, for all active properties, and for each entry's situation. An entry whose situation no area reaches is `unchecked`, and the LLM widens the areas, once per version of the entry.
   - Proof attempts for each pair of property and action. A pair is skipped if a cached proof is still valid, meaning neither the model's code for that action nor the predicate has changed.
5. **Replay on the code.** Each counterexample from the model is run on the real code in a fresh sandbox.
   - Violated on the code: the violation is real.
   - Not violated: the model is wrong. Go back to step 3, and add this trace to the validation set.
6. **Triage.** Decide whether a real violation is a regression or a change of mind (§5).

The developer is never blocked. A step that fails or runs out of time degrades the affected entries to `unchecked` and says so (§8).

## 5. Triage: regression or change of mind

When the developer asks for something new, an earlier constraint can stop applying. EvoCode-Bench labels rounds as correction or conflict for this reason. A companion that reports every violation to the agent would push the agent to undo what the developer just asked for. So every violation reproduced on the real code goes through triage:

- **Regression.** No action in the trace was asked for or changed in this turn's request, and nothing the developer said this turn bears on the constraint. The violation goes to the agent.
- **Possible change of mind.** The trace uses behavior the developer asked for this turn, and the request plausibly conflicts with the constraint. The developer is asked, with the scenario: "You asked for X. Here is a case where X breaks rule Y from turn 3. Keep Y or drop it?" Keeping it means the violation goes to the agent. Dropping it means the entry is retired.

The decision uses one mechanical signal and one judgment:

- the signal is which of the trace's actions were out of date in step 2;
- the judgment is an LLM given the developer's latest message, the constraint and the trace.

The same mechanism serves the agent. If the agent believes a reported constraint was overridden, it can say so through a tool (`dispute_constraint(id, reason)`). That sends the question to the developer instead of letting the agent ignore the report. The developer is the only authority on intent (Lahiri).

## 6. Synthesizing and repairing the model and adapter

- **Inputs** for the LLM: the schema, the code, the ledger, recent trace-validation failures, and the Lean errors from `lake build`.
- **Representative values.** The model uses small domains, and γ maps each abstract value to a concrete one. The LLM partitions concrete values into classes the code treats differently. For example, Forge treats `"category=TYPE_A"` specially, so it must be its own class. Trace validation checks the partition by drawing random members of each class. If two members of a class behave differently, the class is split.
- **When α loses too much.** If the real behavior depends on something the schema cannot see, the model cannot follow the code deterministically. Trace validation then fails on the same abstract trace with different concrete values. The response is to refine the schema (add a field or split a domain), with the developer's nouns as a guide.
- **Time and randomness.**
  - Time is an abstract action `advance(n)`. It is carried out through the application's own clock setting if it has one, otherwise through `libfaketime`.
  - IDs from the application go through the ID map.
  - If time cannot be controlled, time-dependent properties are marked `unobservable`.
- **Structure.** One Lean definition per action, so proofs and caching work per action.
- **How the LLM works.** It runs as an agent (Claude Code headless, with file and shell tools) in the application's workspace (§9). It reads the program's code and runs trace validation itself until there are no mismatches. Its tasks are in `companion/prompts/`. Both tasks say to follow the code even where it looks wrong, because the developer's intent is in the properties. The repair task gives it the code's diff since the model last matched, the disagreeing traces, the unreached changed statements, unreached situations, and the ledger's predicates, whose names it must keep.
- **After a repair.** Predicates that no longer typecheck get one LLM repair round, and predicates that could not be formalized before are retried. A repair that leaves a model that does not build is undone.

## 7. Checking and proving

- **BFS.** Explore reachable states, skipping states already seen, and return the shortest counterexample. It runs as a compiled Lean executable.
- **Search per area.** One scope over every command of an application is far too large. The search is split into areas, each with a small scope over the commands that interact. In the forge app, the areas are allocations, units, modules and access. Each area has 11 to 1,713 states at depth 8, and each is searched in under 0.1 s.
- **Choosing the scope.** The scope needs at least two values of every field a property compares, or some bugs cannot occur at all. Each added value multiplies the states: one more category grew a scope from 56,000 states to 447,000.
- **Transition properties** are evaluated on every explored transition.
- **Proofs.** For each pair of property and action, the goal is `P s → step s a = .ok (s', o) → P s'`. The LLM writes the proof, mostly using `grind`, `simp` and `omega`, under a time budget per goal.
  - If the proof fails and BFS also finds no counterexample, the property may be true but not inductive. The LLM proposes a stronger auxiliary invariant, which is itself checked by BFS before use.
  - If that also fails, the level is `bounded(k)`.
- **Vacuity check.** A constraint that is never exercised gives false confidence. So each ledger entry names its situation, as a predicate on transitions, and the situation is compiled into a probe property that fails exactly where it occurs. BFS's counterexample to the probe is a shortest trace that reaches the situation. If no area reaches it, the entry is `unchecked`, not checked, and the LLM is asked to widen the areas. In the first live run, without this check, two constraints the code breaks were reported checked (synthesis-and-repair.md).

## 8. How developer and agent interact with it

- **Confirmation questions**, in the style of TiCoder. Only for entries with low confidence. Each shows a scenario of two or three steps in the application's own terms, where the candidate property and its nearest alternative disagree, and asks whether it should be allowed. BFS over the model finds the scenario. At most one question per turn. In the evaluation, the simulated developer answers from the ground truth.
- **Report to the agent.** Delivered through a `Stop` hook that returns `decision: block`. It contains:
  - the constraint in the developer's words, with the turn it came from;
  - the trace as real commands;
  - the expected and observed results.

  Reports are deduplicated per constraint, limited to 3 per turn, and capped by the retry budget through `stop_hook_active`.
- **Report to the developer.** `/constraints` shows the ledger with the assurance levels.
- **Failing open.** Companion failures never block the agent. They appear as `unchecked` entries in the report.

## 9. Packaging and architecture

The companion ships as a Claude Code plugin:

- hooks: `UserPromptSubmit`, `PostToolUse`, `Stop`;
- slash commands: `/constraints`, `/confirm`;
- a small MCP server with `dispute_constraint` and `constraint_status` for the agent.

A background daemon per session keeps the Lean build and the caches warm, so the `Stop` hook only waits for the check itself. The design does not depend on the coding agent: a different agent needs only new hook bindings.

Built so far (the plugin packaging, slash commands, MCP server and daemon are not):

```
WS/                        an application's workspace (companion new)
  companion.json           program, Lean namespace, check and agent settings
  lean/                    Lake project: Companion core (copied), <NS>.lean and <NS>/ (the
                           LLM-written model), Ledger.lean (generated), Main.lean and
                           ModelMain.lean (executables with and without the ledger)
  adapter.py               γ, α, ID map (LLM-written)
  validate.py              trace validation, for the LLM to run
  state/                   ledger, messages, report, validation cache, the code at the last
                           match (for change impact), extra validation traces, agent logs

companion/                 this repository: Python package
  hooks.py                 Claude Code hooks
  ledger.py extract.py     stage 1
  workspace.py sync.py coverage.py validation_cache.py trace_validation.py runner.py
                           stage 2: synthesis, repair, change impact, trace validation
  check.py                 stages 3 and 4: search, replay on the code, evidence
  llm.py prompts/          LLM backends and the agent's tasks
lean/Companion/            generic Lean core: Model, BFS, replay, trace selection, JSON I/O
```

Planned: `prove.py`, `triage.py`, `pbt.py` (the PBT side-car, with the same adapter and predicates), and runners for the benchmarks.

The generic Lean interface, as implemented in `lean/Companion/Core.lean`:

```lean
structure Model (σ α ο θ : Type) where
  init    : σ
  step    : σ → α → σ × ο         -- errors are outputs
  observe : σ → θ                 -- what the read interface shows
  inScope : σ → Bool              -- the small scope for search
```

Properties are predicates on transitions `(before, action, out, after)` of observations, so the same predicate is evaluated on the model and on real runs. Each application gets one Lean executable, with these subcommands, all JSON in and out:

- `replay` runs a trace on the model, for trace validation;
- `paths` picks traces for trace validation;
- `check` searches for property violations;
- `eval` evaluates the properties on observed real transitions. The PBT side-car will use it, so both side-cars check identical predicates.

## 10. Build order

The core is what the evaluation needs. The extensions are what makes it a tool.

| Milestone | Tool | Evidence |
| --- | --- | --- |
| Before report 1 | **Done:** Lean core (`Model`, BFS, replay, trace selection, `eval`). A hand-built model and adapter for the whole forge app, 16 properties and 7 proofs. | **Done:** feasibility ([feasibility-study.md](feasibility-study.md)) and the preliminary study ([preliminary-study.md](preliminary-study.md)). |
| Oct 30 (report 2) | **Done early:** LLM synthesis and repair of the model and adapter, change impact, situations (vacuity), ledger extraction, hooks and the `Stop` feedback loop. Still to do: PBT side-car. | LLM-written model against the hand-built one. Agent baseline runs. Study A (controlled detection) started. First RQ1 numbers. |
| Nov 18 (report 3) | Proof attempts and auxiliary invariants, triage, the assurance report. | Study B (end-to-end sessions), RQ4 overhead. |
| Dec 9 (final) | Confirmation questions, dispute tool, `/constraints`. | Our own scenarios if time allows. Write-up. |

Confirmation questions come last on purpose. In the evaluation, the simulated developer only needs to answer them, so building the full interaction can wait.

## 11. Risks

- **Constraints about fine output detail make the model large.** For example, a constraint on an exact output format means the model has to produce that format. Response: measure model size against the number and kind of constraints, and report it.
- **The model cannot be made to conform.** Some applications have state that is hard to observe or a lot of incidental behavior. Response: the `unchecked` and `unobservable` levels make the failure visible, and RQ4 measures how often checks fail.
- **Proofs fail on realistic models.** A model that updates state step by step needs auxiliary invariants. Response: `bounded(k)` is still useful, and the proof rate is a reported result. In the forge prototype, all 7 attempted invariants were proved without auxiliary invariants. That is encouraging, but those were hand-written proofs on a hand-written model.
- **PBT detects just as much.** That is plausible on small state spaces. Response: as in §1, the ledger, the evidence levels and triage do not depend on winning RQ2.
- **Cost per turn.** Synthesis and repair use LLM calls. Response: change impact and caching, and RQ4 measures the cost.
- **Trace validation misses a stale model.** If the traces never reach the changed behavior, a model that has fallen behind the code still passes. Response: select traces by transition coverage, repair on unreached changed code, require that search reach each constraint's situation, and record escapes (evaluation.md §9).
- **The adapter is large and trusted.** In the prototype it was 60% of the model's size, and it held the subtle problems: tie order, IDs that can't be represented, response envelopes. Response: generic adapter parts move into a shared library. Adapter errors get their own checks, such as confirming that an action translated out and back is unchanged.

## Appendix A. Why not translate Python

[Strata-Python](https://github.com/strata-org/Strata-Python) translates Python into Laurel, and then into Strata Core in Lean. Its Pylate subset has 40 named rejections, each measured against CPython, and it models external APIs through PySpec `@admit`. The subset rejects every `import`, plus `open`, `getattr`, `property` and `classmethod`. Strata's own document calls lifting the import ban "the single most valuable rule to lift". The forge app's reference solution would be rejected at its first line. We cite Strata-Python as prior work for the translation we decided not to build.
