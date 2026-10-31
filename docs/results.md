# Results

Oct 30, 2026. First RQ2 numbers (Study A on forge, below). No results yet for RQ1, RQ3 or RQ4.

## Preliminary study: EvoCode-Bench published runs (13 models, 26 tasks)

| Model | Regression rate | Regressions | Requirements failed in the round that introduced them |
| --- | --- | --- | --- |
| Opus 4.8 | 0.2% | 12 | 113 |
| GPT-5.5 | 2.2% | 79 | 380 |
| Other 11 models | 2.4–10.6% | 52–280 each | 412–779 each |
| All | | 2,034 | 6,916 |

Kinds of regressed requirements (sample of 150, one labeler):

| COMPUTE | STATE | FORMAT | INPUT | OTHER |
| --- | --- | --- | --- | --- |
| 49% | 29% | 11% | 10% | 1% |

Failures of new requirements are not labeled.

## Hand-built model of forge (SlopCodeBench, about 1,800 lines of Python)

Model about 600 lines, adapter about 350 lines, 16 properties, 7 proofs (all hold).

**Trace validation**

| Target | Traces / steps | Mismatching traces |
| --- | --- | --- |
| Checkpoint 8, random + shortest paths | 528 / 5,689 | 0 |
| Checkpoint 8, transition coverage | 2,072 / 9,090 | 0 |
| Checkpoint 7 (lacks checkpoint 8 features) | 528 / 5,689 | 333 |
| Units-only model, checkpoints 5–8 | 600 / 5,130 each | 0 (after a 4-line adapter change at checkpoint 7) |

**Seeded bugs** (mismatching traces)

| Bug | Random (528) | Transition coverage (2,072) |
| --- | --- | --- |
| Bind without duplicate guard | 0 | 47 |
| Bind not atomic | 0 | 83 |
| Flash accepts any image | 0 | 51 |
| Tag duplicate keys allowed | 0 | 40 |
| Delete by names partial | 2 | 33 |
| Remove keeps assignment | 3 | 15 |
| Module not linked | 51 | – |
| System scope allowed | 44 | – |
| tenant_id at every revision | 65 | – |
| TYPE_A not doubled | 36 | – |

Detected: 6/10 with random traces, 10/10 with transition coverage. Only the intended property fired in every case.

**Search, then replay on the real program**

| Case | Violation found | Length | Reproduces |
| --- | --- | --- | --- |
| Unmodified checkpoint 8 | blueprint deleted under its allocations | 3 | yes |
| Unmodified checkpoint 8 | silent rebind | 4 | yes |
| Bind without duplicate guard | guard property | 4 | yes |
| Delete by names partial | rejected-request property | 2 | yes |
| Flash accepts any image | flash property | 3 | yes |

**Search cost:** 56,473 states in 4.6 s; with one more category value, 447,025 states in 45–54 s.

## LLM-written models (Haiku 5.5, one run each)

| Run | Time | Cost | Result |
| --- | --- | --- | --- |
| Standalone synthesis | 9 min | $0.29 | 0 / 3,181 traces mismatch (graded independently); 28/29 commands; 7/10 seeded bugs detected |
| Tool `synthesize` | 30 min | $0.67 | 0 / 995 traces mismatch (not graded independently) |
| Repair: new command with a bug | 83 s | $0.03 | 10 unreached statements → model extended; c1 violated, reproduced |
| Repair: dropped guard | 19 s | $0.01 | 14 mismatching traces → model fixed; c1 violated, reproduced |
| Widen search to reach situations | 66 s | $0.02 | c2, c3 had been falsely reported "checked"; now all 3 constraints violated, reproduced |

| Booking example (60-line CLI, not forge): synthesis | 1.9 min | $0.03 | 0 / 452 traces mismatch |
| Booking example: repair after `move` is added without the overlap check | 65 s | $0.02 | model extended; overlap constraint violated, reproduced |

Check per turn, hand-built model: about 20 s for the first check, about 3 s after an edit (cached).

## Study A: controlled detection on forge (RQ2, first run)

Forge checkpoint 8 and its 10 seeded bugs (`make_mutants.py`). Both checkers check the same
14 specification properties on what the real program did, through the same adapter, with the
same number of processes (2, on an 8-core laptop). The two `[extra]` properties are ignored.

- **Lean:** for the 3 bugs that have a model that follows them (`Forge/Variants.lean`), the
  model's bounded search, with counterexamples replayed on the code. Then traces chosen from
  the model's state space, shortest first (transition coverage at depth 0, 1, 2, ...).
- **PBT:** Hypothesis, random traces of 1–20 actions, properties checked after every step,
  failures shrunk. *Same actions*: the ~50 actions of the Lean checker's search areas.
  *Wide*: the 611-action validation alphabet.

| | Lean | PBT, same actions | PBT, wide |
| --- | --- | --- | --- |
| 30 s per bug | 8/10 | 3/10 | 1/10 |
| 300 s per bug | **10/10** | **4/10** | – |
| False alarms on the unmodified code | 0 | 0 | 0 |

Time to detection (s) and length of the violating trace (steps; PBT after shrinking), 300 s budget:

| Seeded bug | Lean | PBT, same actions |
| --- | --- | --- |
| Bind without duplicate guard | 0.3 (4), model search | missed (978 traces) |
| Bind not atomic | 8.9 (3) | missed (996) |
| Delete by names partial | 0.3 (2), model search | 18.5 (2) |
| Flash accepts any image | 0.4 (3), model search | missed (1,013) |
| Module not linked | 4.1 (2) | 1.8 (2) |
| Remove keeps assignment | 34.0 (4) | missed (995) |
| System scope allowed | 1.7 (1) | 0.8 (1) |
| Tag duplicate keys allowed | 43.4 (4) | missed (993) |
| tenant_id at every revision | 4.1 (2) | 46.5 (2) |
| TYPE_A not doubled | 4.1 (2) | missed (1,005) |

Every Lean detection was the property the bug was seeded to break. At 30 s Lean missed
the two bugs that need three setup steps (it reached depth 3); at 300 s it reached depth 4–5.

**What it shows.** Bugs that need a particular 2–4-step setup (bind twice, remove after bind,
tag twice, a TYPE_A blueprint and then an allocation from it) are found by systematic search
in seconds and missed by random search with 10 times the budget. Restricting PBT to the same
actions as the Lean checker raises it from 1 to 3 detections at 30 s, so most of the gap
comes from the search, not from the smaller action set. Where both find a bug, it needs at
most 2 steps, and PBT is sometimes faster.

**Caveats.**
- One application and 10 bugs, seeded by us; the plan is about 6 applications and 300 bugs.
- Only 3 bugs have a model that follows them; for the other 7 the unmodified model only
  chooses the traces. The companion would keep its model in sync with the changed code.
- One seed per PBT process; one run per configuration.
- The first 300 s PBT run of "Flash accepts any image" was interrupted (257 traces); the
  table shows the rerun.

Reproduce (after `fetch_data.sh`, `make_mutants`, `lake build` and `pip install -e ".[dev]"`):

    python -m studies.detection.run --budget 30 --checkers lean pbt
    python -m studies.detection.run --budget 300 --checkers lean pbt
    python -m studies.detection.run --budget 30 --checkers pbt --pbt-actions wide

Results: `studies/detection/results/`.
