# Feasibility Prototype: Lean Models of the Forge App

Oct 1–2, 2026. Code: [lean/](../lean/) and [companion/](../companion/). Experiments: [studies/feasibility/](../studies/feasibility/).

## Question

Can we build a Lean model of real application code that passes trace validation, and does it keep passing as the code changes over checkpoints? Once it passes, do search, replay on the code and proofs work on it?

Part 1 models a small slice of the app (units and the ledger). Part 2 models the whole app. The repository keeps only Part 2's code: its model covers units as well, so it replaced Part 1's. Part 1's code is archived locally in `data/archive/` (not in git), and the file names in Part 1 refer to that version.

## Part 1: units and the ledger

### What was built

The target is SlopCodeBench's `forge`: units, their activation and the capability ledger, as in checkpoints 4–5. It is run as a black box through its command line, using the reference solutions.

`forge` is one of SlopCodeBench's benchmark tasks: a small command-line app for hardware blueprints, allocations and units, which an agent builds over 8 checkpoints. It is unrelated to the Forge paper (Wei et al. 2026) in our related work. Here it stands in for the kind of app a vibe coder would build.

| Component | Lines (code only) | What it does |
| --- | --- | --- |
| `lean/Companion/Core.lean` | 80 | Generic: the `Model` structure, replay, breadth-first search for property violations, picking traces to validate |
| `lean/Forge/Model.lean` | 150 | State, 7 kinds of action (including invalid inputs and list filters), outputs, `step`, observation, two search scopes |
| `lean/Forge/Props.lean` | 51 | 7 properties of the kind a developer would state (below) |
| `lean/Forge/Proofs.lean` | 28 | 4 theorems about `step` |
| `lean/Forge/Variants.lean` | 30 | Models that follow two of the seeded bugs |
| `lean/Forge/Main.lean` | 47 | JSON command line: `alphabet`, `paths`, `replay`, `check`, `eval` |
| `companion/adapters/forge.py` | 162 | Turns abstract actions into real commands, and real output into the model's terms |
| `companion/trace_validation.py` | 61 | Runs traces on the code and on the model, compares them, evaluates properties on the real run |

These are the properties. Each is a Lean predicate on an observed transition, so the same predicate runs on the model and on the real application:

1. The ledger lists exactly the capabilities of active units.
2. An inactive unit reserves its full capacity, and an active unit reserves nothing.
3. Every parent refers to an existing unit.
4. A rejected request leaves the state unchanged.
5. Get, list and ledger leave the state unchanged.
6. Activate succeeds only on inactive units, and leaves them active.
7. `unit list` is ordered by host, then category, then creation.

**The abstraction:**
- A unit's abstract ID is its position in creation order.
- Categories, hosts and capabilities are small numbers, which the adapter maps to `cat-n`, `host-n` and `cap-n`.
- `created_at`, `manufacturer` and `module_uuids` are not modeled.
- The types in the model are unbounded. Small scope comes from the set of actions search draws from.

**Traces used for validation:**
- 300 shortest paths to states in the search scope;
- 300 random traces of 5–20 actions over a wider scope, which has 1,294 distinct actions.

That is 600 traces and 5,130 steps. After every step, the adapter observes the whole state through `unit list` and `ledger capabilities`.

### Results

#### 1. The model passes trace validation, and keeps passing through checkpoint 8

| Reference solution | Traces that mismatch | Notes |
| --- | --- | --- |
| Checkpoint 5 | 0 / 600 | The checkpoint the model was written for |
| Checkpoint 6 | 0 / 600 | Adds modules attached to units. Unit behavior is unchanged, so the model needs no change. |
| Checkpoint 7 | 600 / 600 | Every response is wrapped in a `revision` envelope. The adapter can't read the output. |
| Checkpoint 7, adapter updated | 0 / 600 | A 4-line change to the adapter: unwrap the envelope |
| Checkpoint 8 | 0 / 600 with the updated adapter | Adds access control for blueprints, which doesn't affect units |
| Checkpoint 4 | 600 / 600 | Sanity check: this checkpoint has no ledger or activation, so the checkpoint 5 model does not apply |

Across checkpoints 5–8, the code changed three times and **the model never needed to change. The one change was in the adapter.** Each validation run of 600 traces takes about 42 s with 8 parallel workers, which is about 16,000 command-line calls.

#### 2. Seeded bugs are caught, and by the right property

Six bugs were seeded into the checkpoint 5 solution, one per property, each a one-place edit (`make_mutants.py`). The reference model was validated against each:

| Seeded bug | Traces where model and code disagree | Property violated on the real runs |
| --- | --- | --- |
| The ledger includes inactive units | 113 / 600 | the ledger property (169 transitions) |
| Activate without its status guard | 9 / 600 | the activation property (10) |
| Create doesn't check that the parent exists | 226 / 600 | the parent property (1,595) |
| Reserved capacity always reported as 0 | 181 / 600 | the reserved-capacity property (306) |
| Invalid capabilities are stored before being rejected | 72 / 600 | the rejected-request property (90) |
| List sorted by category before host | 1 / 600 | the list-order property (2) |

In each case, only the property the bug was aimed at fired. There were no false alarms on the four correct checkpoints.

**Two bugs are rare under random traces:** activation without its guard (9 of 600) and the list order (1 of 600).

**For those two, we ran the companion's actual loop.** First, the model was updated by hand to follow the buggy code (`Variants.lean`). Then:
1. The updated model passes trace validation against the buggy code, with 0 of 600 traces mismatching.
2. Search over the model finds the shortest violation:
   - activation: `create unit; activate it again`, 2 steps;
   - list order: `create (host 1, category 0); create (host 0, category 1); list`, 3 steps.
3. Replaying each sequence on the real buggy code reproduces the violation.

#### 3. Search cost depends sharply on the scope

| Search scope | Distinct states at depth 6 | Time, all 7 properties |
| --- | --- | --- |
| 3 units, 1 category, 2 hosts, 2 capacities, 3 capability sets | 56,473 | 4.6 s |
| Same, with 2 categories | 447,025 | 45–54 s |

Adding one category value multiplied the states by 8. But with only one category, the list-order bug can't happen at all. **The scope needs at least two values of every field that a property compares**, and choosing the scope is a real design decision.

#### 4. Proofs go through on this model

The proofs live in `Forge/Proofs.lean`. All four hold for any number of units and sequences of any length, and use only Lean's standard axioms:

- A rejected request changes nothing. Case split, then `grind`.
- Reads change nothing.
- Reserved capacity matches status.
- Every parent exists. This one is preserved by every action and needs the invariant itself as a hypothesis, which makes it the realistic kind. It took one hint to `grind`: unfolding the parent-exists check.

The file checks in under a second. These are local invariants. None needed an auxiliary invariant, which remains the open question for proofs.

## Part 2: the whole forge app

### What was built

`lean/Forge/` models the checkpoint 8 reference solution as a whole:

- **Blueprints:** create, get by ID or by name, delete by ID or by a list of names.
- **Allocations:** expanded from blueprints, with TYPE_A requirement sets doubling. Then binding, with its duplicate-assignment guard, its order of error checks and tenants, and deletion by assignment or by ID list.
- **Units and the ledger.**
- **Modules:** firmware images and flashing, with flashing's order of error checks.
- **Tags:** unique keys per module.
- **The schema lifecycle.**
- **Revision negotiation:** the `revision` field on every response, `tenant_id` shown only from revision 1.1, get-by-name only from 1.2.
- **Access control:** role and scope rules for blueprint create and delete.

Left out: `check readiness` and plugin configuration, which depend on configuration files rather than application state.

| Component | Lines (code only) |
| --- | --- |
| `Forge/Model.lean` | 595, of which about 100 are search scopes and the validation alphabet |
| `Forge/Props.lean` | 94: 14 properties from the specification, and 2 "extra" ones |
| `Forge/Proofs.lean` | 71: 7 theorems |
| `Forge/Variants.lean` | 51: models that follow three of the seeded bugs |
| `companion/adapters/forge.py` | 355 |

The reference solution is about 1,800 lines of Python. The model is about a third of that, and the adapter about a fifth.

**The model follows the code, not the specification.** For example:
- in bind, the tenant checks come before the invalid-operation check;
- access checks come before payload validation;
- a "known firmware image" is one currently on some module;
- deleting a blueprint leaves its allocations in place.

**The two extra properties** are rules a developer could plausibly state but the specification does not:
- every allocation's blueprint still exists;
- binding never moves an allocation that is bound to another assignment.

The reference code breaks both, so they test whether the companion finds violations in unmodified code.

**Search** runs per area (allocations, units, modules, access), because one search over all 38 commands would be far too large.

### Results

**Trace validation against the real code.**

| Target | Traces / steps | Traces that mismatch |
| --- | --- | --- |
| Checkpoint 8, random and shortest-path traces | 528 / 5,689 | 0 |
| Checkpoint 8, transition coverage (below) | 2,072 / 9,090 | 0 |
| Checkpoint 7, which lacks checkpoint 8's features | 528 / 5,689 | 333, all on the schema commands and on access flags for blueprint create and delete, which checkpoint 7 doesn't have |

**Seeded bugs.** Ten bugs were seeded into checkpoint 8 (`make_mutants.py`), one per specification property. Columns show traces where model and code disagree, with the number of property violations seen on the real runs in parentheses.

| Seeded bug | Random + paths (528) | Transition coverage (2,072) |
| --- | --- | --- |
| Bind without the duplicate-assignment guard | 0 | 47 (guard property 47) |
| Bind applies known operations before rejecting unknown ones | 0 | 83 (rejected-request property 83) |
| Flash accepts any image | 0 | 51 (flash property 51) |
| Tag create allows duplicate keys | 0 | 40 (tag-key property 40) |
| Delete by names removes the resolved names before failing | 2 | 33 (rejected-request property 33) |
| Remove keeps the assignment | 3 | 15 (bound-iff-assigned property 15) |
| Module not recorded on its unit | 51 | – |
| System scope allowed | 44 | – |
| `tenant_id` shown at every revision | 65 | – |
| TYPE_A not doubled | 36 | – |

The last four were caught well by random traces, so they weren't rerun with transition coverage. In every case, only the intended property fired.

**Random traces missed four of the ten bugs entirely.** Each of those needs setup before the buggy step: a blueprint, then allocations, then a bind, then a second bind. Random actions rarely build that.

**Transition coverage** catches all of them. It takes a shortest path to every reachable state in each area's search scope (up to depth 4) and extends it by every action of that area. It costs about 1.6 times the steps of the random set, about 4 minutes with 8 workers.

**How traces are chosen decides what trace validation can see.** A model that has fallen behind the code can still pass validation if the traces never reach the changed behavior.

**The companion's loop on the full model** (`search_and_confirm.py`, depth 8):

| Case | Model that follows it, against the real code | What search found | Reproduces on the code |
| --- | --- | --- | --- |
| Unmodified checkpoint 8 | 0 / 2,072 mismatches (above) | [extra] blueprint deleted under its allocations, 3 steps; [extra] silent rebind, 4 steps | yes, both |
| Bind without the duplicate guard | 0 / 528 | the guard property, 4 steps | yes |
| Partial delete by names | 0 / 528 | the rejected-request property, 2 steps | yes |
| Flash accepts any image | 0 / 528 | the flash property, 3 steps | yes |

The search areas are small: 11 to 1,713 states at depth 8, each searched in under 0.1 s.

**Proofs** (`Forge/Proofs.lean`). All hold for any number of resources and sequences of any length, and use only Lean's standard axioms:

- A rejected request changes nothing, across all 38 commands. This went through a lemma about the command runner; the case split takes about 10 s.
- An allocation is bound exactly when it has an assignment. Preserved by every action.
- No module has two tags with the same key. Preserved by every action. This needed two list lemmas by hand: appending a fresh pair keeps keys distinct, and filtering keeps them distinct.
- A bind that adds to an assignment succeeds only if no allocation is bound to it.
- Blueprint create and delete never succeed in system scope.
- With an access rule, only an admin can create or delete blueprints.

**Two practical lessons from the proofs:**
- The small enums had to derive decidable equality, not plain boolean equality, so that `simp` could evaluate the comparisons.
- Splitting every hypothesis in every case timed out. Splitting only where needed did not.

## What this means

- **Feasible for the whole app, not just a slice.**
  - A model about a third the size of the program follows it exactly, on 9,090 steps.
  - It survives later checkpoints: units unchanged through checkpoint 8 in Part 1.
  - Where the code changes, trace validation points to which features changed: the checkpoint 7 run.
- **The companion's full loop works end to end**, on unmodified code and on bugs:
  1. the model, updated to follow the code, passes validation;
  2. search finds a short violating sequence;
  3. the sequence reproduces on the real program.
- **It finds real cross-feature problems in unmodified code**, if the developer states the rule. In the reference solution, deleting a blueprint strands its allocations, and binding silently moves an allocation from another assignment.
- **The adapter is a major piece of work, and it is trusted.**
  - In Part 1 it was as large as the model. In Part 2 it is about 60% of the model's size.
  - It absorbed the only change across checkpoints in Part 1.
  - It held the subtle problems: tie order for timestamps and random UUIDs, IDs that can't be represented, response envelopes.
- **Trace selection is the weak point of trace validation.**
  - Random traces missed 4 of 10 seeded bugs.
  - Transition coverage over the search areas caught all of them, at modest cost.
  - The companion should select traces by transition coverage, and the evaluation should measure how many bugs slip past validation (RQ4).
- **Search finds what random traces miss.** In Part 1 that meant bugs random traces hit in 1 or 9 of 600 traces; in Part 2, four bugs they never hit. This is an early sign for RQ2, with the caveat that we seeded these bugs.
- **Proofs work for the app's real guarantees.** Atomicity of rejected requests across every command, binding consistency, unique tag keys and access control were all proved, in about 70 lines. None needed an auxiliary invariant. Tag uniqueness needed hand-written list reasoning.

## Not tested yet

- **An LLM writing the model and adapter from the code.** Here they were written by Claude in conversation, with the spec and code in hand. The automated synthesis-and-repair loop doesn't exist yet, and this is now the largest open question.
- **Code written by an agent,** as opposed to the reference solutions.
- **A non-Python application.** The EvoCode-Bench key-value store needs Go, which is not installed here.
- **Constraint extraction and the Claude Code hooks.**

## Reproducing

Part 2:

```
studies/feasibility/fetch_data.sh                    # SlopCodeBench problems into data/
(cd lean && lake build)                              # model, properties, proofs, forge-model
python3 -m studies.feasibility.make_mutants          # seeded bugs into data/forge-mutants/
python3 -m studies.feasibility.validate --checkpoints 7 8
python3 -m studies.feasibility.validate --mutants
python3 -m studies.feasibility.validate --transitions --depth 4 --checkpoints 8
python3 -m studies.feasibility.validate --transitions --depth 4 --mutants bind-no-duplicate-guard \
    bind-not-atomic flash-any-image tag-duplicate-allowed delete-names-partial remove-keeps-assignment
python3 -m studies.feasibility.search_and_confirm
```

Results are written to `studies/feasibility/results/`.
