# Live Lean Companion for Vibe Coding: Project Brief

Oct 15, 2026

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

What is not built yet is listed under [Plan](#plan).

## Evaluation

As in report 1; the details are in [docs/evaluation.md](docs/evaluation.md), and why it differs from the proposal's evaluation is in its §1.

No existing benchmark combines multi-turn evolution, persistent application state, constraints carried across turns, and publicly runnable tests. So we adapt the stateful tasks of SlopCodeBench (forge, dynamic_config_service_api, trajectory_api) and EvoCode-Bench (lsm-kvstore-engine, multiservice-deploy-pipeline, mlpipe-orchestrator, mlops-inference-gateway): a simulated developer delivers each specification change conversationally, and the checkpoint specifications are the ground truth. Two studies:

- **Controlled detection,** which runs no agents: seeded mutants of the reference solutions, plus the regressions from our own agent runs.
- **End-to-end sessions,** with five configurations: agent-only, a constraint reminder, a PBT side-car, the Lean companion, and an oracle gate as the upper bound. Sonnet is the main agent and Haiku the weaker one; each configuration runs three times per task.

Research questions:
- **RQ1, formalization fidelity:** how accurately constraints become Lean invariants, and how reliably revised or contradicted ones are updated or retired.
- **RQ2, violation detection:** at equal invariants and time budget, does bounded model checking over the Lean model find violations that property-based tests on the code miss?
- **RQ3, regression mitigation:** does the companion's feedback reduce regressions over a session, against the other four configurations?
- **RQ4, overhead:** latency, tokens and failure rate of synthesis, repair and checking per turn, and how they scale.

RQ2 uses both studies; the others use the sessions. A tie on RQ2 is still a result: the contribution then rests on the ledger and its evidence levels.

## Plan

### Milestones

| Date | Milestone | Status |
| --- | --- | --- |
| Oct 14 | Report 1: the design and the evaluation plan | Submitted. The studies and the tool's results are not in it. |
| Oct 30 | Report 2 | Contents not decided. |
| Nov 18 | Report 3 | Wanted: the controlled detection study, the end-to-end sessions, models for two or three more apps, triage, and the hooks tried in live sessions. |
| Dec 9 | Final report | Results for RQ1–RQ4, and the write-up. |

### Open work

Wanted, with no date of its own. The evaluation needs all of it.

**For the evaluation**
- **Ground-truth annotations.** The constraints each checkpoint states, with revisions, for the chosen tasks; written by someone who is not building extraction. RQ1 and RQ2 depend on them.
- **Agent baseline runs.** Sonnet and Haiku, no side-car, on the seven tasks above, through SlopCodeBench's native runner; the code saved after every round. They give the regression rates and the corpus of real regressions. Needs: Docker, Harbor, Go, and an agreed API budget.
- **PBT side-car.** Hypothesis stateful testing through the same adapter, checking the same compiled invariants. Needs: `hypothesis`.
- **Simulated developer** and **oracle gate.** Neither exists yet.
- **Mutants for the other tasks.** Forge has 10.
- **Second labeler for the preliminary study.** 40–50 of the requirements in `studies/preliminary/labels.csv`, labeled blind, so agreement (Cohen's kappa) can be reported.

**For the tool**
- **Other applications.** Only forge (Python) has been run. The EvoCode-Bench tasks are mostly Go.
- **Synthesis beyond one run.** Stronger models than Haiku, and repeated runs to measure variance.
- **Triage** of a change of mind. Today a violation is only labeled "stated this turn" or "regression".
- **Confirmation questions** to the developer.
- **Proofs in the loop.** In the design, but not in report 1's technique or evaluation.

### Proposed, not decided

- **A first live Claude Code session** with the hooks, on forge, before the benchmark runs.
- **Skip for now** what the evaluation does not need: plugin packaging, a background daemon, and `/constraints`.
- **Split of work.** Ground truth should not come from whoever builds extraction, so one of us takes the work independent of the tool (annotations, second labels, baseline runs, PBT side-car) and the other the tool.
- **Our own scenarios:** multi-turn sessions built on existing apps, such as BaxBench's access-control backends, if the external benchmarks give too little.
- **Adapter hardening:** move the generic parts (ID mapping, envelopes, tie order) into a shared library, and check the adapter itself, for example that an action translated out and back is unchanged.
- **Housekeeping:** verify the author list of the Veil entry in `refs.bib`.

## Open questions and risks

- **Can an LLM write the model and adapter for other applications?** For forge, yes, even with Haiku. Other applications, especially non-Python ones, are untested.
- **How specific are the extracted situations?** A constraint is only as well checked as its situation is specific: a vague one is reached easily and hides a narrow search. This is part of RQ1.
- **The adapter is trusted and large.** Its errors can hide real differences.
- **Strong agents rarely regress,** so RQ3 may show a small effect with Sonnet. Then Haiku carries RQ3, and Sonnet's rate is reported.
- **PBT may detect as much** (RQ2).
- **Choosing the search scope.** Too small and bugs can't occur; each added value multiplies the states.
- **Benchmark instructions are more explicit than real conversation.** The simulated developer paraphrases them into vaguer messages.
- **The preliminary study's numbers rest on one labeler** until the second labels are in.
- **Cost per turn** (RQ4).

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
| [docs/approach.md](docs/approach.md) | The approach stated formally (definitions, guarantees and their assumptions), and how the code compares with report 1 |
| [docs/evaluation.md](docs/evaluation.md) | The evaluation plan |
| [docs/preliminary-study.md](docs/preliminary-study.md) | What regressions agents make (EvoCode-Bench) |
| [docs/feasibility-study.md](docs/feasibility-study.md) | The forge prototype and its results |
| [docs/synthesis-and-repair.md](docs/synthesis-and-repair.md) | LLM-written models: synthesis, repair, and situations |
| [report-1.pdf](report-1.pdf) | Report 1, as submitted: the design and the evaluation plan. It supersedes the proposal's evaluation and stage 2. (The proposal itself is in git history.) |
