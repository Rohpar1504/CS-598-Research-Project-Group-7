# Live Lean Companion for Vibe Coding: Project Brief

Oct 13, 2026

## The project in short

The companion runs alongside a coding agent during a vibe coding session. It keeps a list of the constraints the developer has stated, keeps an executable Lean model of the application, and after every agent turn checks the model against the constraints. It reports to the agent only violations it has reproduced on the real code.

The problem. Over a long session the developer states constraints, such as "no two bookings in the same room may overlap." Later edits break constraints agreed earlier, and a developer who is vibe coding is unlikely to notice. Mitchell and Shaaban call this constraint-reconciliation decay and propose a formal side-car as the remedy.

What the companion works with:

- **Constraints.** Anything the developer states that can be checked through the application's interface: invariants over its state, rules about what actions do, how inputs are handled, what outputs look like. Each is a Lean predicate.
- **Model.** An executable Lean description of how each action changes the state and what it returns. It follows the code.
- **Adapter.** Python that runs an abstract action on the real application and reads its state and outputs back in the model's terms.

The design is a hybrid in FuzzMo's terms. The model follows the code, and the constraints follow the developer, so a violation means the code does something the developer said it must not.

The coding agent is Claude Code. The companion talks to the application only through its interface, so it does not depend on the application's language.

## The design

The four stages of the proposal stand. What changed is how stage 2 is done, and some details. The full design is in [docs/design.md](docs/design.md).

1. **Constraint extraction.** Constraints from the conversation become Lean predicates, kept in a ledger with where each was said and its status (proposed, confirmed, superseded or retired). Uncertain ones are confirmed with the developer through a concrete example.
2. **Model maintenance.** An LLM writes and repairs the Lean model and the adapter. **Trace validation** runs the same action sequences on the code and the model and compares outputs and states; a mismatch triggers repair. This replaces the term "conformance replay" from the proposal.
3. **Checking.** Exhaustive search over short action sequences in a small scope, plus attempts to prove that each action preserves each constraint, which then holds for sequences of any length. A constraint counts as checked only if the search reaches the situation it is about.
4. **Feedback.** A violating sequence is replayed on the real code. If it reproduces, it goes to the agent, unless the developer's latest request may have changed the constraint, in which case the developer is asked (triage: regression or change of mind).

Decisions made since the proposal:

- **No Python-to-Lean translation.** It already exists: AWS's Strata-Python translates a Python subset into a Lean-based verification language. Its subset rejects every `import`, so it would reject the code agents write. The argument that the model matches the code is trace validation instead, the same standard as Cedar's Lean model tested against Rust.
- **The companion runs after each agent turn, not each edit.** In the middle of a turn the code is legitimately half-changed.
- **Each constraint gets an evidence level:** proved, checked to depth k, violated with a reproduction, or not checked. The answer to the proposal's question, "how can we gain confidence that vibe-coded software does what the developer asked for?", is this ledger with evidence, not only the regressions caught.
- **Traces for validation are chosen by transition coverage,** not at random (see the prototype below).
- **Changed code that no validation trace reaches also triggers repair.** Trace validation cannot see a command the model does not know, so a new feature would otherwise go unmodeled.
- **Each constraint records its situation:** when it could be broken. Search must reach that situation before the constraint is reported checked; otherwise the LLM widens the search areas.

## Where we are

### Preliminary study: what regressions do agents make?

From EvoCode-Bench's published results: 13 models, 26 tasks, 2,034 regressions. Write-up: [docs/preliminary-study.md](docs/preliminary-study.md).

- **Strong models rarely regress.** Opus 4.8 breaks 0.2% of the requirements it had been passing. GPT-5.5 breaks 2.2%, and the other models 2.4–10.6%.
- **What breaks.** In a random sample of 150 regressed requirements:
  - 49% are the output of a single run, computed from its input;
  - 29% are behavior across a sequence of actions, concentrated in a few tasks;
  - 11% are output format;
  - 10% are input handling.

  All of these are within the companion's scope.
- **The broken requirements were stated** in the round's instructions, in all 15 spot-checked.
- **Regressions are not concentrated in rounds where the user corrects an earlier requirement:** 40% of regressions fall in such rounds, against 54% of rounds.

### Feasibility prototype: the forge app

The target is SlopCodeBench's `forge` app (no relation to the Forge paper): a command-line app for blueprints, allocations, units, modules and tags, about 1,800 lines of Python. Write-up: [docs/feasibility-study.md](docs/feasibility-study.md).

**What was built:**
- a Lean model of the whole app, of about 600 lines;
- an adapter of about 350 lines;
- 16 properties;
- 7 proofs.

The model and adapter were written by hand, by Claude with the spec and code.

**Results:**
- **The model matches the real program exactly**, on 9,090 steps against checkpoint 8. An earlier model of units survived three later checkpoints unchanged. When code lacks features, trace validation points to which ones.
- **The full loop works:**
  1. the model, updated to follow the code, passes validation;
  2. search finds a 2–4 step violating sequence;
  3. the sequence reproduces on the real program.

  This held for seeded bugs, and for two cross-feature problems in the unmodified code. Deleting a blueprint strands its allocations, and binding silently moves an allocation from another assignment. The spec allows both, but a developer could easily forbid them.
- **Proofs work for the app's real guarantees,** for any number of resources and any sequence length:
  - a rejected request changes nothing, across all 38 commands;
  - binding consistency;
  - unique tag keys;
  - access control.

  None needed a strengthened invariant.
- **Two weak spots.**
  - Random validation traces missed 4 of 10 seeded bugs, because those bugs need setup first. Transition coverage caught all 10, so trace selection matters.
  - The adapter is about 60% of the model's size, held most of the subtle problems, and is trusted. It needs as much care as the model.

### LLM-written models: synthesis and repair

Claude Haiku 5.5, at low effort, writes the model and adapter of forge from its code alone, and keeps them in step with the code. Write-up: [docs/synthesis-and-repair.md](docs/synthesis-and-repair.md).

- **Synthesis works.**
  - **Standalone run.** 9 minutes, $0.29. Graded independently: 0 mismatching traces out of 3,181, 28 of 29 commands covered, and 7 of 10 seeded bugs noticed. The three misses are in features it left out.
  - **The tool's own `synthesize`.** 30 minutes, $0.67. 0 mismatches on 995 traces.
- **Repair works.** Two edits were run live:
  - **A new command, with a bug.** Validation still passed, but the unreached changed code made the companion call the agent. The agent added the command to the model (83 s, $0.03), and search then found the broken constraint.
  - **A dropped check.** 14 traces disagreed. The agent fixed the model (19 s, $0.01), and the broken constraint was reported with a reproduction.

  In both cases the agent modeled the bug, as instructed, and the constraint check caught it.
- **A hole, found and closed.** Two constraints the code breaks were reported "checked", because the LLM's search areas never reached the situations they are about. Constraints now carry their situation, and an unreached one is reported unchecked and sent to the agent. After the agent widened the areas (66 s, $0.02), all three live constraints were correctly reported violated.

### The tool

`companion` (Python, plus the Lean core) runs the whole loop. A test suite specifies its behavior on the real forge program, with stand-ins for the LLM. Commands:

- `new` and `synthesize`: a workspace per application, and its LLM-written model and adapter.
- `extract`: developer messages become ledger entries, each with a predicate and a situation, typechecked in Lean, with one LLM repair round.
- `check`:
  1. trace validation with transition coverage, cached by which code each trace ran (about 3 s per turn after an edit);
  2. repair of the model when it has fallen behind;
  3. search per area, with each violation replayed on the real program;
  4. evidence for each constraint.
- `hook`: the Claude Code `UserPromptSubmit` and `Stop` hooks. `Stop` blocks the agent, at most once per turn, with reproductions of broken constraints.

Not built yet:
- triage of a change of mind (only "stated this turn" versus "regression");
- confirmation questions;
- proofs in the loop;
- the PBT side-car.

The hooks have not yet been run in a live Claude Code session.

## Evaluation

The full plan is in [docs/evaluation.md](docs/evaluation.md). The evaluation as submitted had problems. Each is now resolved:

| Problem | Resolution |
| --- | --- |
| No existing benchmark combines multi-turn evolution, application state, constraints carried across turns, and being runnable. We checked SlopCodeBench, EvoCode-Bench, Vibe Code Bench, BaxBench, MT-Sec, CodeIF-Bench and SysMoBench. | Use the stateful tasks of SlopCodeBench (forge, dynamic_config_service_api, trajectory_api) and EvoCode-Bench (lsm-kvstore-engine, multiservice-deploy-pipeline, mlpipe-orchestrator, mlops-inference-gateway). Optionally add multi-turn sessions built on existing apps, such as BaxBench's access-control backends, as MT-Sec did. |
| "Stateful" in EvoCode-Bench means a persistent workspace, and most regressions are not about state. | The companion's scope covers state, outputs and input handling, which is all of what breaks. Results are reported per kind. |
| Strong models rarely regress, so end-to-end mitigation (RQ3) may have little to work on. | Sonnet is the main agent and Haiku the weak one. If Sonnet regresses too rarely, Haiku carries RQ3 and Sonnet's rate is reported. The headline claims rest on the controlled study (RQ2, RQ4) and the ledger (RQ1). |
| Benchmark tests are the wrong ground truth: they mostly check things nobody stated, or formats. | Our own ground truth: annotated constraints, a reference Lean model (forge's exists), and an independent invariant oracle. Benchmark tests are labeled stated / not stated and reported separately. |
| No baseline for simply reminding the agent of the constraints. | Added a constraint-reminder configuration. |
| The PBT comparison mixed translation with checking. | The PBT side-car uses the same ledger, adapter and compiled predicates. Only the checking method differs. |
| Per-round pass/fail hides what we care about. Opus "fails" kvstore while passing 203 of 204 cases. | The main metric is the regression rate per requirement, normalized by opportunity. |
| Published agent runs can't be replayed: they contain no code. | Real regressions come from our own runs, with the workspace saved every round. Seeded bugs fill the rest, as in the prototype. |
| SlopCodeBench's Harbor conversion leaks hidden tests. | Use its native runner. |
| Huang et al.'s numbers are function-level. | Cited only for function-level code. |

Research questions:
- **Preliminary:** done.
- **RQ1:** formalization and triage.
- **RQ2:** detection against PBT on the code and bounded search on the code, at equal budget.
- **RQ3:** mitigation over a session, against agent-only, constraint reminder, PBT side-car and an oracle gate.
- **RQ4 (new):** assurance. How often does "proved" or "checked" fail on the code anyway, and how often does a stale model pass validation?
- **RQ5:** overhead.

A tie on RQ2 is still a result.

## Next steps

Two statuses: **wanted** means we want it, at the date given; **proposed** means it has been suggested but not decided.

### Wanted by Oct 14 (report 1)

- **Report 1.** Present the design, the preliminary study and the forge prototype.
- **Second labeler for the preliminary study.** Someone other than the first labeler labels 40–50 of the requirements in `studies/preliminary/labels.csv`, blind, so agreement (Cohen's kappa) can be reported. Until then the study's numbers rest on one labeler.
- **LLM-written model and adapter.** Done for forge, with Haiku at low effort; see above. Still open: other applications, stronger models, and repeated runs to measure variance.
- **Constraint extraction (RQ1).** A simulated developer delivers forge's checkpoints as conversation, and the extracted constraints are scored against ground-truth annotations. Needs: the annotations, written by someone who is not building extraction, and API access.
- **Agent baseline runs.** Sonnet and Haiku, with no side-car, on the stateful tasks: SlopCodeBench's forge, dynamic_config_service_api and trajectory_api, through its native runner; EvoCode-Bench's lsm-kvstore-engine, multiservice-deploy-pipeline, mlpipe-orchestrator and mlops-inference-gateway. The code is saved after every round. This gives the regression rates and the corpus of real regressions. Needs: Docker, Harbor, Go, and an agreed API budget.
- **PBT baseline.** Hypothesis stateful testing, driven through the same adapter and checking the same compiled predicates. Needs: `hypothesis` installed.

### Wanted later

- **Oct 30, report 2:** open. Its earlier items moved to Oct 14.
- **By Nov 18, report 3:**
  - the Claude Code loop: triage, and the hooks and feedback to the agent (built) tried in live sessions;
  - models for two or three more apps;
  - Study A (controlled detection on seeded and mined bugs);
  - Study B (end-to-end sessions).
- **Dec 9, final:** results for RQ1–RQ5, and the write-up.

### Proposed, not decided

- **Skip for now** the things the evaluation does not need: plugin packaging, a background daemon, `/constraints`, and the confirmation dialog. (The tool's commands, caching and the detection of unreached changed code are built.)
- **A first live Claude Code session** with the hooks, on forge, to see the loop with a real coding agent before the benchmark runs.
- **Split of work.** Ground truth should not come from whoever builds extraction. So one of us takes the work that is independent of the tool: the second labels, the ground-truth annotations, the agent baseline runs and the PBT baseline. The other takes model synthesis and extraction.
- **Our own scenarios:** multi-turn sessions built on existing apps, such as BaxBench's access-control backends, if the external benchmarks give too little.
- **Adapter hardening:**
  - move the generic parts (ID mapping, envelopes, tie order) into a shared library;
  - add checks of the adapter itself, such as confirming that an action translated out and back is unchanged.
- **Housekeeping:** verify the author list of the Veil entry in `refs.bib`.

## Open questions and risks

- **Can an LLM write the model and adapter well?** For forge, yes, even with Haiku (see above). Other applications, especially non-Python ones, are untested.
- **How specific are the extracted situations?** A constraint is only as well checked as its situation is specific: a vague one is reached easily and hides a narrow search. This is part of RQ1.
- **The adapter is trusted and large.** Its errors can hide real differences. It needs its own checks, and shared code across apps.
- **Strong agents rarely regress,** so RQ3 may show a small effect with Sonnet.
- **PBT may detect as much.** The contribution then rests on the ledger, evidence levels and triage.
- **Choosing the search scope.** Too small and bugs can't occur; each added value multiplies the states.
- **Benchmark instructions are more explicit than real conversation.** The simulated developer paraphrases them into vaguer messages for RQ1.
- **The preliminary study had one labeler.** A second person should check a subset of `studies/preliminary/labels.csv` before its numbers are reported.
- **Cost per turn** (RQ5).

## Related work and how we differ

Each piece of the companion has a neighbour. No published work puts them together and runs them on every turn.

| Work | What it does | How we differ |
| --- | --- | --- |
| [Vibe Reasoning](https://arxiv.org/abs/2511.00202) (Mitchell, Shaaban, LMPL 2025) | Position paper proposing a side-car that formalizes the user's constraints and validates the code. The prototype does syntactic checks on TypeScript. | We build it with a semantic model, search and proofs. |
| [Forge](https://arxiv.org/abs/2606.22413) | Closed loop: extracts models from generated Java and turns verification failures into corrections. | Needs requirements written in advance and restricted Java. |
| [VibeContract](https://arxiv.org/abs/2603.15691) | Vision paper: contracts per task, validated by developers. | Our constraints are about the application, not tasks. |
| [Specula](https://arxiv.org/abs/2607.25333) | Agents write TLA+ models of system code, validate them on traces and model-check them. | Offline, hours per system, invariants mostly from the code. |
| [Quint](https://github.com/quint-co/quint), [LLM Kit](https://github.com/quint-co/quint-llm-kit), [Connect](https://github.com/quint-co/quint-connect) | Executable specs, agent skills that write them, and trace replay against Rust. | Invoked on demand. |
| [FuzzMo](https://arxiv.org/abs/2501.12972) | LLM-written Quint models of smart contracts, checked by replaying traces. | Source of the code-based / ideal-based framing. |
| [Cedar](https://dl.acm.org/doi/abs/10.1145/3663529.3663854) (FSE 2024) | Hand-written Lean model, with the production Rust tested against it. | Our standard for "the model matches the code". No LLM, no per-turn loop. |
| [Strata-Python](https://github.com/strata-org/Strata-Python) (AWS) | Translates a Python subset into a Lean-based verification language, with semantics measured against CPython. | The translation we decided not to build. Its subset rejects imports. |
| [SysMoBench](https://arxiv.org/abs/2509.23130) | Benchmark of LLM-written TLA+ models, scored by trace validation against real systems. | Offline, distributed systems. Its metrics fit our model synthesis. |
| Alloy, LLM2Alloy, SmallCheck | The small-scope hypothesis and exhaustive testing of small inputs. | Our search follows the hypothesis. |
| [Intent Formalization](https://arxiv.org/abs/2603.17150) (Lahiri), [TiCoder](https://arxiv.org/abs/2208.05950), [nl2postcond](https://arxiv.org/abs/2310.01831) | Turning intent into checkable specifications. Confirming intent through tests. | We take one instance: constraints from the conversation, confirmed by example. |
| Kiro, Spec Kit | Specs written up front. Kiro turns properties into PBT. | Kiro's approach is our PBT baseline. |
| [Cherny's post](https://x.com/bcherny/status/2102543349102338309), [AfterVibe](https://www.alphaxiv.org/abs/2607.09900), [Lee, Wu, Kim](https://www.alphaxiv.org/abs/2607.09072), [FVSpec](https://arxiv.org/abs/2606.01008) | Lean models of state machines on chosen hotspots; specifications recovered after the fact; Lean proofs against PBT from shared templates; Python tests transpiled into Lean. | One-off or offline, without checking the model against the code. |
| Benchmarks: [SlopCodeBench](https://arxiv.org/abs/2603.24755), [EvoCode-Bench](https://arxiv.org/abs/2605.24110), [Vibe Code Bench 1–100](https://www.vals.ai/benchmarks/vcb-1-100), [BaxBench](https://arxiv.org/abs/2502.11844), [MT-Sec](https://arxiv.org/abs/2510.13859), [CodeIF-Bench](https://arxiv.org/abs/2503.22688) | Multi-turn evolution, stateful backends, and instruction retention, each separately. | None has all four of: multi-turn evolution, application state, constraints carried across turns, and being runnable. |
| [Huang et al.](https://arxiv.org/abs/2607.01855) | Regressions accumulate over multi-turn conversations; the Verification Gate mitigates them. | Function-level tasks. Source of our oracle-gate baseline. |

## Files

| File | What it is |
| --- | --- |
| [README.md](README.md) | Repository layout and how to run things |
| [docs/design.md](docs/design.md) | The tool's design |
| [docs/evaluation.md](docs/evaluation.md) | The evaluation plan |
| [docs/preliminary-study.md](docs/preliminary-study.md) | What regressions agents make (EvoCode-Bench) |
| [docs/feasibility-study.md](docs/feasibility-study.md) | The forge prototype and its results |
| [docs/synthesis-and-repair.md](docs/synthesis-and-repair.md) | LLM-written models: synthesis, repair, and situations |
| CS_598_…pdf | The submitted proposal. The evaluation and stage 2 in it are superseded by this brief. |
