# Lean Companion: Evaluation Design

Oct 12, 2026. Settled. It replaces the evaluation in the submitted proposal; the project brief summarizes it. The tool design is in [design.md](design.md). The preliminary study it relies on is in [preliminary-study.md](preliminary-study.md).

## 1. Problems with the evaluation as submitted

Each problem comes with its evidence. Section 3 onward fixes them.

1. **Few benchmark tasks keep application state.**
   - Of SlopCodeBench's 36 problems, about 10 keep state between actions, and 3 have clear rules about it: forge, dynamic_config_service_api and trajectory_api.
   - In EvoCode-Bench's "26 stateful tasks", "stateful" means the workspace and the agent session persist across rounds. 24 of the 26 are in Go.
   - In the preliminary study, regressions about app state come mostly from four EvoCode-Bench tasks: lsm-kvstore-engine, multiservice-deploy-pipeline, mlpipe-orchestrator and mlops-inference-gateway. 12 of the 25 tasks in the sample have no state regressions at all.
2. **Regressions are spread across kinds.** In a random sample of 150 regressed requirements:
   - 49% (±8) are about the output of a single run, computed from its input;
   - 29% (±7) are about behavior across a sequence of actions;
   - 11% are about output format;
   - 10% are about input handling.

   All four kinds are about what the application outputs, so all are in scope (design.md §1). For single-run outputs, the model has to reproduce the computation itself, so they are covered only as far as the model can express it.
3. **The strongest Claude model barely regresses.**
   - Opus 4.8 breaks 0.2% of the requirements it had been passing (10 of 4,187).
   - GPT-5.5 breaks 2.2%, and the other models 2.4% to 10.6%.
   - Sonnet is not in the published data. If Sonnet regresses as rarely as Opus, RQ3 has almost nothing to mitigate.
4. **Huang et al. does not transfer.** Its numbers come from HumanEval+ and MBPP+, which are function-level.
5. **There is no baseline that only reminds the agent.** Injecting the extracted constraints with no checking might prevent most regressions.
6. **The PBT comparison mixes translation with checking.** If each side-car translates the constraints itself, a difference in results could come from the translation and not from the checking method.
7. **Benchmark runners can leak tests.** SlopCodeBench converted to Harbor exposes hidden tests to the agent. Issue [#30](https://github.com/SprocketLab/slop-code-bench/issues/30) is still open. EvoCode-Bench re-ran everything after fixing a similar Harbor leak ([#1960](https://github.com/harbor-framework/harbor/issues/1960)).
8. **Some prior tests are not rerun.** Some SlopCodeBench checkpoints set `include_prior_tests: false`, for example checkpoints 3–4 of dynamic_config_service_api.
9. **Per-round binary reward hides near-passes.** On kvstore, Opus scores 0 of 8 rounds, yet it passed 203 of 204 cases in round 1. A one-case failure that persists looks like total failure.
10. **The published runs cannot serve as a regression corpus.** EvoCode-Bench publishes the agents' prompts and responses, not their code at each round. Real regressions to check against must come from our own runs.

## 2. Existing benchmarks

What we need is a benchmark with four properties. We found none that has all four.

- **Multi-turn evolution:** the same codebase changes over several rounds of requests.
- **Application state:** the application keeps data between actions, with rules about that data.
- **Constraints carried across turns:** a rule stated early must still hold later.
- **Open and runnable:** we can download the tasks and run them ourselves.

| Benchmark | Multi-turn evolution | Application state | Constraints carried across turns | Open and runnable |
| --- | --- | --- | --- | --- |
| SlopCodeBench | yes, 3–8 checkpoints | about 10 of 36 problems | implicitly, by rerunning earlier tests | yes |
| EvoCode-Bench | yes, 5–15 rounds, with correction and conflict rounds | a few tasks; regressions about state come mostly from 4 | yes, it tracks which requirements are still active | yes, with reference solutions for every round |
| [Vibe Code Bench 1–100](https://www.vals.ai/benchmarks/vcb-1-100) | yes, up to 10 dependent requests on a web app | yes (Supabase) | yes. About a third of its checks are regression checks. "Applying rules consistently across features" is its largest category of requests (34.6%). | Not found. The [public artifacts](https://github.com/vals-ai/vibe-code-bench-cais-2026-artifacts) are for the single-turn VCB v1. Evaluation needs a browser agent. |
| [BaxBench](https://github.com/logic-star-ai/baxbench) | no, a single turn | yes. 28 backend scenarios, about 8 of which test access control or authorization | no | yes |
| [MT-Sec](https://arxiv.org/abs/2510.13859) | 3 turns: expand, edit, refactor | partly, through the tasks derived from BaxBench | security only | yes (Hugging Face) |
| [CodeIF-Bench](https://arxiv.org/abs/2503.22688), [MultiCodeIF](https://github.com/SYSUSELab/MultiCodeIF) | yes | no, mostly function-level | yes, they measure instruction forgetting | yes |
| [SysMoBench](https://arxiv.org/abs/2509.23130) | no | yes, concurrent systems | no | yes. Its trace-validation metrics are the right ones to borrow for checking our models. |

The combination is new. We therefore build on established tasks, as MT-Sec did with BaxBench, instead of inventing applications (§8).

## 3. Research questions

| RQ | Question | Status and notes |
| --- | --- | --- |
| Preliminary | How often do agents break requirements they previously met? What kind are those requirements, and were they stated? | Done for EvoCode-Bench ([preliminary-study.md](preliminary-study.md)). Our own baseline runs (§5) add Sonnet and Haiku. |
| RQ1 Formalization | How accurately does the companion turn constraints in the conversation into properties? How reliably does it revise or retire them? Does it tell a regression apart from a change of mind? | EvoCode-Bench labels its correction and conflict rounds. 40% of regressions fall in such rounds, so triage is exercised but is not the main case. |
| RQ2 Detection | Given the same properties and the same time budget, does model search plus proof find violations that PBT on the code, or bounded search on the code, misses? | Study A. Results are reported per kind (state, compute, format, input). |
| RQ3 Mitigation | Does feedback from the companion reduce regressions over a session, compared with no side-car, a constraint reminder, a PBT side-car and an oracle gate? | Study B. If Sonnet rarely regresses, there is little to mitigate. Then Haiku carries RQ3, and Sonnet's low rate is reported (§5). |
| RQ4 Assurance | When the companion marks a constraint proved or checked to depth k, how often is it violated on the code anyway? How much of the code does trace validation exercise, and how often does a stale model still pass it? | Study A. The forge prototype already shows the risk: random traces missed 4 of 10 seeded bugs, and transition coverage caught all 10. |
| RQ5 Overhead | Latency, tokens, model repair rate, how often entries are `unchecked`, and how these grow with the session. | Study B. |

A tie on RQ2 is still a result. The contribution then rests on RQ1, RQ4 and the constraint ledger itself.

## 4. Ground truth, kept independent of the companion

For each application we evaluate on, we prepare the following. None of it is shared with the companion.

- **Constraint annotations.** For each round, the constraints that are active, with their status (new, revised, superseded or retired). Two people annotate at least half the tasks independently, and we report Cohen's kappa. These are the ground truth for RQ1.
- **A reference model and properties** in Lean, written by us. For the forge app these already exist: the hand-built model from the prototype (`lean/Forge`). They are used to decide whether an extracted property means the same as an annotated one. Two predicates count as equivalent if they agree on every state the reference model reaches up to scope k. This is checked mechanically.
- **An invariant oracle.** A hand-written adapter, the reference properties, and long random traces with a large budget, run on the real code. It is the ground truth for "a constraint is violated" in RQ2–RQ4. It must stay separate from the companion's adapter, which an LLM writes.
- **Labels for benchmark tests.** Every test is labeled with the same kinds as the preliminary study (state, compute, format, input, other) and as stated or not stated. RQ2 and RQ3 report results per label.

The invariant oracle uses random testing, which could favor the PBT side-car. We mitigate this in two ways. The oracle has about 100 times the budget, the ground-truth properties and its own adapter. And the seeded mutants in Study A have known ground truth that does not depend on the oracle.

## 5. Agent baseline runs

These runs give the agent-only baseline, Sonnet's and Haiku's regression rates, and the corpus of real regressions for Study A.

1. **Agent-only runs.** Run Claude Code with no side-car on the four EvoCode-Bench tasks where state regressions concentrate (kvstore, multiservice-deploy-pipeline, mlpipe-orchestrator, mlops-inference-gateway). Also run it on forge, dynamic_config_service_api and trajectory_api, using SlopCodeBench's native runner and not the Harbor conversion.
   - Do this once with Sonnet and once with Haiku.
   - Save the workspace after every round, so that regressions become a corpus for Study A.
2. **Measure** with the preliminary study's scripts:
   - the regression rate per model, normalized by opportunity;
   - the regressions classified by kind.
3. **Use:** Sonnet is the main agent. If Sonnet gives too few regressions for RQ3 to compare configurations, Haiku carries RQ3, and Sonnet's low rate is reported as a finding. This is not a gate on the project.

The EvoCode-Bench tasks are in Go and run in Docker through Harbor, so both need to be installed first.

## 6. Study A: controlled detection (RQ2, RQ4)

This study runs no agents, so it is deterministic, cheap and can be large.

**The regression corpus.** Pairs of a code version and a violated constraint:

- **Mined.** Regressions on stated constraints from our own agent runs, in the baseline runs and in Study B, using the saved workspace snapshots. The published EvoCode-Bench runs cannot be used, because they contain no code.
- **Seeded.** Mutation operators applied to the reference solutions. These exist for every EvoCode-Bench round (`steps/round-N/solution`) and every SlopCodeBench checkpoint. The operators follow the kinds found in the preliminary study:
  - **state:**
    - forget to update derived state;
    - skip a guard on a new path;
    - apply changes before validating, which breaks atomicity;
    - leave a cache or index stale;
    - make a snapshot or read see the wrong version.
  - **compute:**
    - an off-by-one at a boundary;
    - a wrong default;
    - a filter or aggregation applied to the wrong scope;
    - a dropped edge case, such as an empty input or a tie.
  - **format:** a changed output field, ordering or token.
  - **input:** validation skipped on a new path, or errors checked in the wrong order.

  A mutant counts only if the invariant oracle confirms it violates a ground-truth property. This removes equivalent mutants.

  The forge prototype already has 10 such mutants (`studies/feasibility/make_mutants.py`), and its scripts are the template for this study.

**Clean versions.** The reference solutions without mutation, used to measure false alarms and escapes (RQ4).

**What is compared**, at equal wall-clock budgets of 30 s and 300 s:

| Checker | What it does |
| --- | --- |
| PBT on code | Hypothesis `RuleBasedStateMachine`, driven through the adapter, with shrinking |
| Bounded search on code | BFS over real executions through the adapter. It separates "exhaustive" from "on a model". |
| Companion, BFS only | Model plus BFS, with each counterexample replayed on the code |
| Companion, full | Plus proofs and auxiliary invariants |
| Companion, no repair | Ablation: the model is synthesized once and never repaired after failed trace validation |

Each checker runs twice:
- with the ground-truth properties, which isolates the power of the checking method;
- with the properties the companion extracted, which measures the whole pipeline.

Trace validation in every companion configuration uses transition coverage. The prototype showed that random traces let stale models pass.

**Metrics:**
- the fraction of mutants detected, per kind;
- the time until detection;
- the length of the counterexample;
- the rate of model errors, meaning counterexamples on the model that do not reproduce on the code;
- on clean versions: the fraction of properties proved, bounded or unchecked, and the escapes the invariant oracle finds.

**Size.** About 6 applications × about 5 rounds × about 10 mutants, so about 300 mutants, plus the mined regressions. The applications should include kvstore, which carries most of the state regressions.

## 7. Study B: end-to-end sessions (RQ1, RQ3, RQ5)

**Tasks.** Chosen from the preliminary study and the baseline runs:

- **EvoCode-Bench:** lsm-kvstore-engine, multiservice-deploy-pipeline, mlpipe-orchestrator and mlops-inference-gateway. These are the tasks where regressions about state occur. gomlops and nexus are optional. They keep state, but Opus never regressed on them, and the sample has only 3 state regressions from them (2 and 1).
- **SlopCodeBench:** forge, dynamic_config_service_api and trajectory_api, plus mvvault, meshctl or pwd_manager if they pass a screen.
- **The scenarios from §8**, if they are built.

**Configurations.** All use the same agent, the same retry budget per round and the same time budget for the side-car:

1. Agent only.
2. **Constraint reminder.** The ledger's text is injected into the context every turn, with no checking.
3. PBT side-car. It uses the same ledger, adapter and compiled predicates as the companion. Only the checking method differs.
4. Lean companion.
5. Oracle gate, as an upper bound. It reruns the benchmark tests and rolls back on regression, as in Huang et al.

**Metrics:**

- **Primary:** the regression rate per requirement, normalized by opportunity, as in the preliminary study. It is reported per test label (§4).
- **Secondary:**
  - the pass rate at each round;
  - the drop from the first round to the last;
  - violations the invariant oracle finds at the final round.
- **RQ1:**
  - precision and recall of the ledger against the annotations, per round;
  - triage accuracy on correction and conflict rounds;
  - the number of questions asked to the developer.

**How the developer's messages are delivered.** EvoCode-Bench's instructions are detailed specifications of 92 to 3,865 words, which is much more explicit than vibe-coding chat. For RQ1 we run two conditions:

- **as written:** the instructions are given as they are;
- **paraphrased:** a simulated developer splits each instruction into short messages and leaves some details implicit.

SlopCodeBench checkpoints are always paraphrased this way. In both conditions, the simulated developer answers the companion's questions from the annotations.

**Statistics.** The design is paired: the same task and seed across all configurations.

- 3 seeds per configuration.
- Results reported per task.
- Bootstrap confidence intervals over pairs of task and seed.
- No claims of significance from small samples.

Roughly 8 tasks × 5 configurations × 3 seeds is 120 sessions. The baseline runs give the cost per session. If the budget is tight, configurations 2 and 5 run on one seed.

## 8. Our own scenarios (optional)

These are multi-turn sessions written to provoke the kinds of decay the preliminary study found. They are built on existing applications where possible:

- BaxBench's access-control scenarios (secret_storage, shopping_cart_service, credit_card_service), extended to several turns;
- the stateful SlopCodeBench problems, which already have checkpoints. For these we add only annotations and an invariant oracle.

Each scenario has 6–10 rounds written as developer messages:

- early rounds establish constraints;
- later rounds add features that cut across them, such as deletion, bulk import, undo, new roles or expiry;
- at least one round is a correction and one is an implicit conflict.

We write and freeze the scenarios before tuning the companion, and report their results separately from the external benchmarks.

## 9. Threats to validity

- **We built the scenarios and the oracle.** Mitigations: the scenarios are frozen before tuning, the oracle is kept separate, and external benchmarks are reported separately.
- **Agents are nondeterministic.** Mitigations: seeds, a paired design, and Study A, which runs no agent.
- **Results may not transfer between agent models.** The preliminary study shows regression rates ranging from 0.2% to 10.6% across models. We report per model and do not generalize from one agent.
- **The preliminary study had one labeler.** A second person checks a subset before its numbers are reported.
- **Benchmark instructions are more explicit than real conversation.** The paraphrased condition in Study B addresses this.
- **The benchmarks may have leaked into training data.** Both carry canary strings. We cannot control for this, so we say so.
- **Language.** EvoCode-Bench is mostly Go, and SlopCodeBench is Python. We report results per language.
