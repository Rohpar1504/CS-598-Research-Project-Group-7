# Results

Oct 20, 2026. No results yet for RQ1–RQ4.

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

## Tool prototype (`companion`)

About 2,000 lines of Python plus a generic Lean core. Run on forge and on the booking example.

| Stage | Built | Not built |
| --- | --- | --- |
| 1. Constraint extraction | Message → ledger entry (Lean predicate + situation), typechecked, one LLM repair round | Confirmation questions |
| 2. Model maintenance | LLM synthesis and repair of model and adapter; trace validation by transition coverage, cached per changed code; repair on unreached changed code | |
| 3. Checking | Search per area; constraint reported checked only if its situation is reached | Proofs in the loop |
| 4. Feedback | Replay on the real program; `Stop` hook returns reproductions to the agent | Triage (regression or change of mind) |

Not built: the PBT side-car, the simulated developer, the oracle gate. The hooks have not been used in a live Claude Code session.

Tests: 54 (stand-in LLM, real forge program). Last full pass: Oct 20.

Smallest example: [examples/booking](../examples/booking/README.md), the whole loop with Haiku, about 4 min, $0.05.
