# Preliminary Study: What Regressions Do Coding Agents Make?

Oct 1, 2026. Data: EvoCode-Bench's published v2 results (13 models, 26 tasks, 227 rounds). Scripts and labels: [studies/preliminary/](../studies/preliminary/).

## Questions

1. How often do agents break requirements they previously satisfied?
2. What are those requirements about? Is it app state, computed outputs, formats or input handling?
3. Were the broken requirements stated to the agent?
4. Do regressions cluster in rounds where the user corrects or contradicts an earlier requirement?

## Method

**Data.** EvoCode-Bench publishes, for each model, task and round, the requirements that failed. Each task's cumulative test script registers every test case with a line of the form `reg "<case>" <origin-round> <requirement> ...`. From those lines we know which requirements each round tests and when each was introduced.

**Regression.** A requirement that a model passed at round i−1 and fails at round i, where round i−1's tests already covered it. A requirement that fails in the round that introduces it is not counted as a regression. Neither is a requirement that was already failing. This rules out 6,916 failures of new requirements. 75 of the 2,034 regressions involve requirement ids that no `reg` line names; for those, coverage was decided by a word match on the test files instead.

**Regression rate.** For each model: the number of requirements broken at round i, divided by the number it passed at round i−1 that round i tests again. Raw counts would mislead, because a model that fails most requirements outright has little left to break.

**Labels.** The 2,034 regressions involve 646 distinct (task, requirement) pairs. We labeled a seeded random sample of 150 pairs, plus all 11 pairs on which Opus 4.8 regressed, on two dimensions:

- **Kind**, meaning what the requirement is about:
  - `STATE` (behavior across a sequence of actions): what the app keeps between actions, and what later actions do and return because of it. Examples: a snapshot still sees old versions; a registered model appears in the list; a locked environment rejects scaling.
  - `COMPUTE` (output of a single run): an output computed from that run's input. Examples: metrics, data transforms, solvers, compiler output, cache keys.

  Both kinds are about what the application outputs, and both are in the companion's scope. They differ in what the model has to capture. For `STATE` it is the effect of sequences of actions. For `COMPUTE` it is the computation itself, and the search runs over inputs, not sequences.
  - `FORMAT`: the shape, fields or order of the output, or the on-disk format.
  - `INPUT`: validation, parsing and error handling.
  - `OTHER`: anything else, such as the build or the project's own test suite.
- **Manifestation**, meaning how it broke:
  - `WRONG`: the feature runs but gives the wrong result.
  - `BREAK`: the feature is gone or crashes. Examples: an unknown command, a failed build, exit code 127, or a CLI flag that is no longer parsed.

One person (Claude) did all the labeling, from the requirement id, the test intents and the failure message. 4 labels are marked uncertain in `labels.csv`. A second labeler should check at least a subset before these numbers are reported.

**Was it stated?** We read the instructions of the round that introduced the requirement, for 15 randomly chosen labeled pairs.

## Results

### 1. Strong models rarely regress

| Model | Requirements broken / passing and retested | Rate |
| --- | --- | --- |
| Opus 4.8 | 10 / 4,187 | 0.2% |
| GPT-5.5 | 76 / 3,419 | 2.2% |
| GLM-5.2 | 50 / 2,086 | 2.4% |
| MiniMax-M3 | 84 / 2,741 | 3.1% |
| The other 9 models | | 5.3–10.6% |

Opus 4.8 regressed in 10 of 201 later rounds, across 8 tasks. Sonnet is not in the published results.

The 10 in the table and the 12 events in the second table below come from the same data. The difference is the 2 Opus regressions whose coverage was decided by word match; the rate counts only `reg`-registered requirements.

### 2. What the broken requirements are about

The random sample has 150 requirements, which account for 425 regression events. The ± figures are 95% intervals per requirement. Events on the same requirement are correlated, so there are no event-level intervals.

| Kind | Share of requirements | Share of events |
| --- | --- | --- |
| COMPUTE | 49% (±8) | 45% |
| STATE | 29% (±7) | 36% |
| FORMAT | 11% (±5) | 9% |
| INPUT | 10% (±5) | 7% |
| OTHER | 1% | 3% |

- **Manifestation:** 81% (±6) WRONG and 19% (±6) BREAK.
- **Opus 4.8's own 12 events:** 5 COMPUTE, 3 STATE, 2 OTHER (build and test suite), 1 FORMAT, 1 INPUT. 11 of the 12 were WRONG.
- **STATE regressions come from a few tasks.**
  - lsm-kvstore-engine accounts for 16 of the 44 STATE requirements in the sample. It is also the task with the most regressions overall: 662 of 2,034 events.
  - The others are multiservice-deploy-pipeline (5), mlpipe-orchestrator (4) and mlops-inference-gateway (4: three on authorization, one on model creation).
  - 12 of the 25 tasks in the sample have no STATE regressions at all.

### 3. The broken requirements were stated

All 15 spot-checked requirements are described in the instructions of the round that introduced them, sometimes down to exact output tokens. EvoCode-Bench's instructions are detailed specifications of 92 to 3,865 words, not casual messages. So "stated" is easy to meet here, and it will be harder in real vibe-coding sessions.

### 4. Regressions are not concentrated in correction or conflict rounds

54% of rounds after round 1 are labeled correction or conflict, but only 40% of regression events occur in those rounds. Most regressions happen while the agent adds something unrelated, which is the kind the companion should report to the agent. A rule that tells regressions apart from changes of mind still matters, but it is not the main case.

### 5. The published runs cannot be replayed as code

The trajectory archive contains the agents' prompts and responses, not snapshots of the code at each round. A corpus of real regressions to check against therefore has to come from our own runs, with the workspace saved after every round.

## What this means

- **The phenomenon is real for typical models but nearly absent for the strongest Claude model.** With Sonnet as our agent, how much there is to mitigate is unknown until we run it. If Sonnet regresses about as rarely as Opus, an end-to-end comparison (RQ3) will have almost no events to work with.
- **Everything found is in scope, but half of it needs a different kind of model.** About 30% of regressed requirements are about behavior across sequences of actions, concentrated in a few tasks, kvstore above all. About half are outputs of a single run, where the model has to reproduce a computation, such as a formula, a transform or an ordering, and exploring sequences of actions does not help. How well the model can express such computations decides how much of these regressions the companion catches.
- **Almost everything broken was a stated requirement**, under this benchmark's detailed instructions. The constraints exist to be extracted. How well extraction works from vaguer conversation is RQ1's job, and the simulated developer has to be realistic for that answer to mean anything.
- **Per-round binary reward hides what we care about.** kvstore looks like a total failure for Opus, at 0 of 8 rounds, yet Opus passed 203 of 204 cases in round 1. Our metrics should be per requirement.

## Limitations

- Only one person labeled, from short failure messages. Some labels between COMPUTE and STATE are judgment calls, mostly for pipeline runners that record runs.
- EvoCode-Bench only. SlopCodeBench publishes no per-test agent results, so it needs our own runs.
- Regressions are decided from failures alone. If a requirement's id changes between rounds, a real regression would be counted as a new failure, so the rates are a lower bound.
- The "stated" check covered 15 pairs.

## Reproducing

```
studies/preliminary/fetch_data.sh          # downloads EvoCode-Bench tasks and results into data/
python3 studies/preliminary/extract_regressions.py   # regressions.csv, new_failures.csv
python3 studies/preliminary/sample_pairs.py          # sample.csv (seed 598)
python3 studies/preliminary/summarize.py             # tables above, from labels.csv
```
