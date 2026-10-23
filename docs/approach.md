# Lean Companion: The Approach, Stated Formally

Oct 23, 2026. This document defines the objects the companion works with and what each step computes. For every result the companion reports, it says what that result guarantees and which assumptions the guarantee needs. Section 8 compares this approach, and the code as it is today, with the approach in report 1 (`report-1.pdf`, §3–4). Section 9 lists what to change next.

Where design.md describes something the code does not do yet, this document follows the code and marks the difference. Notation: $\Sigma^{\le k}$ is the set of sequences of length at most $k$ over $\Sigma$, $w \cdot a$ appends $a$ to $w$, and $\mathrm{Pref}(T)$ is the set of prefixes of the traces in $T$.

## 1. Setting

A **session** is a sequence of turns $t = 1, \dots, n$. In turn $t$:

1. the developer sends a message $m_t$;
2. the coding agent edits the program from $P_{t-1}$ to $P_t$;
3. the companion runs once on $(L_{t-1}, m_t, P_t)$ and produces a new ledger $L_t$, a report $R_t$ and possibly feedback $F_t$ for the agent.

In Claude Code, the `UserPromptSubmit` hook records $m_t$, and the `Stop` hook runs step 3 (`companion/hooks.py`).

### 1.1 The program

The program is a black box with an interface:

$$P = (C,\ c_0,\ \mathit{Act}_P,\ \delta_P,\ \mathit{read}_P)$$

- $C$ is the set of concrete states (in practice, the program's data directory);
- $c_0$ is the empty state;
- $\mathit{Act}_P$ is the set of concrete actions: a command line with its standard input;
- $\delta_P : C \times \mathit{Act}_P \to C \times \mathit{Out}_P$ runs one action and returns the new state and the raw output;
- $\mathit{read}_P : C \to \mathit{Raw}$ is everything the read commands show (`list`, `get`, ...).

The companion never inspects $C$ directly. Everything it knows about the state comes through $\mathit{read}_P$.

**Assumption A1 (determinism).** Running the same sequence of concrete actions from $c_0$ gives the same outputs and reads, apart from the values the adapter abstracts away (generated IDs, timestamps). The validation cache needs a stronger version, **A1′**: the behavior depends only on the program's source and the trace, not on other files, the clock or the environment (`companion/validation_cache.py`).

### 1.2 The model

The model is the `Model` structure of `lean/Companion/Core.lean`:

$$M = (S,\ s_0,\ A,\ O,\ \Theta,\ \mathit{step},\ \mathit{observe})$$

- $\mathit{step} : S \to A \to S \times O$ is total and deterministic. A rejected request is an output (an error), not a failure.
- $\mathit{observe} : S \to \Theta$ is the model's version of $\mathit{read}_P$.

$M$ is written by an LLM, and it follows the code, not the developer (the FuzzMo distinction between code-based and ideal-based models). The developer's intent lives only in the constraints (§2).

### 1.3 The adapter

The adapter is $D = (\gamma, \alpha_O, \alpha_\Theta, \iota)$:

- $\iota$ is the ID map from abstract IDs (positions in creation order) to the real IDs the program generated.
- $\gamma(a, \iota)$ turns an abstract action into a concrete one.
- $\alpha_O$ and $\alpha_\Theta$ translate raw outputs and reads into $O$ and $\Theta$, or into $\bot$ (`inexpressible`, `companion/abstraction.py`) when the model's types cannot express them.

$\iota$ grows as the program creates resources, so the concrete trace depends on the whole abstract prefix. Write $\gamma^*(w)$ for the concrete trace that the adapter runs for an abstract trace $w$.

### 1.4 Runs and transitions

For an abstract trace $w = a_1 \cdots a_k$, the **program's abstract run** runs $\gamma^*(w)$ from $c_0$ and reads the state after every step:

$$\rho_P(w) = (\theta_0,\ (o_1, \theta_1),\ \dots,\ (o_k, \theta_k)), \qquad \theta_i = \alpha_\Theta(\mathit{read}_P(c_i)),\quad o_i = \alpha_O(\mathit{out}_i).$$

The **model's run** $\rho_M(w)$ is defined the same way from $s_0$, with $\theta_i = \mathit{observe}(s_i)$. This is the `replay` subcommand.

Each step of a run gives a **transition**

$$\tau_i = (\theta_{i-1},\ a_i,\ o_i,\ \theta_i) \in \mathcal{T} = \Theta \times A \times O \times \Theta.$$

This is the `Transition` structure. The model and the program produce transitions of the same type, so one predicate can judge both.

## 2. Constraints and the ledger

### 2.1 Constraints

A **constraint** is a decidable predicate $\varphi : \mathcal{T} \to \mathbb{B}$ (`Property` in Core.lean). A state invariant $I$ is the special case $\varphi(\tau) = I(\theta')$, where $\theta'$ is the state after the step.

Two consequences of this definition are easy to miss:

- **Constraints only see what the interface shows.** $\varphi$ reads $\Theta$, never $S$. A rule about state that no read command exposes cannot be stated. This is the formal version of design.md's "decidable from what the application shows".
- **Constraints look at one step.** $\varphi$ sees a single transition. "`rev` increases by 1 on every change" fits, and so does "after `DELETE k`, `GET k` returns NOT_FOUND", because the state after the delete shows that $k$ is gone. A rule about history, such as "a deleted name is never reused", fits only if $\Theta$ records that history. Otherwise the model would need ghost state, and ghost state is invisible to the program's side of the comparison. Report 1 calls all constraints "invariants". The accurate term is single-step safety properties over observations.

### 2.2 Situations

The **situation** of a constraint, $\psi_\varphi : \mathcal{T} \to \mathbb{B}$, holds on the transitions where $\varphi$ is put to the test. For "when X, then Y", it is X. The intended relation between the two is

$$\neg\varphi(\tau) \implies \psi_\varphi(\tau) \qquad \text{(W1: every violation happens in the situation)}$$

so that a search that never reaches $\psi_\varphi$ cannot have found a violation of $\varphi$ either. Today, W1 is a convention the extractor is asked to follow, and nothing checks it (§9, item 2).

### 2.3 The ledger

A ledger entry is

$$e = (\mathit{id},\ \mathit{text},\ \varphi_e,\ \psi_e,\ \mathit{kind},\ \mathit{status},\ \mathit{confidence},\ \mathit{provenance},\ \mathit{history}).$$

Its status is one of proposed, confirmed, superseded, retired or unformalized. The entry is **active** if its status is proposed or confirmed. $\psi_e$ is optional. The ledger $L_t$ is a finite set of entries plus the turn counter (`companion/ledger.py`).

**Ledger update** (stage 1). Let $V(M)$ be the vocabulary: the Lean source of the model's types and helper functions. Extraction is an LLM call

$$\mathcal{E}(L_{t-1}, m_t, V(M)) = \mathit{ops} \in \{\mathsf{add}, \mathsf{revise}, \mathsf{retire}, \mathsf{supersede}\}^*$$

and then $L_t = \mathrm{tc}(\mathrm{apply}(L_{t-1}, \mathit{ops}))$. Here $\mathrm{tc}$ elaborates every active $\varphi_e$ and $\psi_e$ against $M$. An entry that fails gets one LLM repair round with Lean's error. If it still fails, it becomes unformalized. `apply` keeps three invariants:

- an entry's ID never changes;
- `history` and `provenance` are only ever appended to;
- a revision resets the status to proposed.

Formalization depends on the model: $\varphi_e$ is a term over $M$'s types. When $M$ is repaired, entries are typechecked again, and unformalized entries are retried, because the new model may have the names they need.

## 3. Conformance: does the model follow the code?

**Definition 1 (conformance).** $M$ conforms to $P$ through $D$ on a trace $w$, written $M \models_D w$, if $\rho_M(w) = \rho_P(w)$ component by component, with no $\bot$. For a set of traces, $M \models_D T$ if $M \models_D w$ for every $w \in T$.

The comparison is made at every step, so $M \models_D T$ implies $M \models_D \mathrm{Pref}(T)$. The ideal is $M \models_D A^*$: equality of observable behavior through the adapter on every trace. With A1 and a deterministic model, that is a bisimulation on observables. Only a finite $T$ can be tested.

### 3.1 The validation set

$$T_t = \mathrm{Cov}_{d}(M_t) \,\cup\, \mathrm{Rnd}_{n} \,\cup\, X_t$$

(`validation_traces`, `companion/sync.py`):

- **Transition coverage.** For each search area $(\Sigma_i, B_i)$ (§4):
  $$\mathrm{Cov}_d = \bigcup_i \{\, \pi_i(s) \cdot a \;:\; s \in \mathrm{Reach}^i_{\le d},\ a \in \Sigma_i \,\}$$
  Here $\pi_i(s)$ is one shortest path to $s$, found by BFS. Every transition $(s, a)$ of the area's bounded graph up to depth $d$ is run once. Default $d = 4$ (`transition_depth`).
- **Random traces.** $n = 200$ seeded random traces of length 5 to 20 over the wider validation alphabet $\Sigma_V$.
- **Disproved counterexamples.** $X_t$ collects the traces on which the model claimed a violation that the code did not reproduce (§5). It only grows.

$\mathrm{Cov}_d$ is computed from the model, so it covers the model's transitions, not the program's. A command or branch the model does not know is in no trace. §3.2 closes this gap.

### 3.2 Change impact

Let $P_b$ be the program at the last turn where the model conformed (the baseline, `state/synced.json`). Let $\Delta_t$ be the statements of $P_t$ that are new or changed relative to $P_b$, counting only statements inside functions, and let $\mathrm{Lines}(T)$ be the statements that some trace in $T$ executed (recorded by `companion/runner.py`). The **gap** is

$$\mathrm{Gap}_t = \Delta_t \setminus \mathrm{Lines}(T_t)$$

(`companion/coverage.py`). A non-empty gap triggers repair even when $M \models_D T_t$, because validation can say nothing about code that no trace runs.

### 3.3 Repair

Repair is a loop of at most $r = 2$ rounds (`repair_rounds`). In each round the LLM agent edits $(M, D)$, given the source diff $P_b \to P_t$, the mismatching traces, $\mathrm{Gap}_t$, the unreached situations (§4.2) and the ledger's predicates, whose names it must keep. The loop ends as soon as the model conforms. If the final model does not build, the previous one is put back. When the model conforms again, $P_t$ becomes the new baseline.

The model is never asked to match the developer's intent. Repair only ever moves $M$ toward $P_t$.

### 3.4 Caching

Validation reuses the program's run $\rho_P(w)$ from an earlier turn unless the change $P_{t-1} \to P_t$ touches a function that $w$ executed, touches module-level code, or changes the adapter's source.

**Proposition 0 (the cache changes no outcome).** Assume A1′, and assume line tracing records every function a run enters. Then a reused $\rho_P(w)$ equals what running $w$ on $P_t$ would give.

*Sketch.* Under A1′, the run of $w$ is determined by the code it executes. If no function on its path changed, and no module-level code changed, the run follows the same path through the same statements. Adding a new top-level definition invalidates nothing until existing code is changed to call it, and that change is inside a function the trace executed. `tests/test_cache.py` checks this property empirically on forge edits.

## 4. Bounded checking

### 4.1 Search areas and BFS

One search over every command is far too large, so the search is split into **areas**. An area is $(\Sigma_i, B_i)$: a small action alphabet $\Sigma_i \subseteq A$ and a scope bound $B_i : S \to \mathbb{B}$ (`Area` in `lean/Companion/Cli.lean`). The LLM chooses them when it writes the model.

$\mathrm{Reach}^i_{\le k}$ is the set of states reachable from $s_0$ in at most $k$ steps of $\Sigma_i$ whose states all satisfy $B_i$. The examined transitions are

$$E^i_k = \{\, (s, a) : s \in \mathrm{Reach}^i_{<k},\ a \in \Sigma_i,\ B_i(\mathit{step}(s, a).1) \,\}.$$

$\mathrm{BFS}(M, \Sigma_i, B_i, \varphi, k)$ (`bfs` in Core.lean) returns a shortest trace $w \in \Sigma_i^{\le k}$, staying inside $B_i$, whose last transition violates $\varphi$, or nothing if there is none.

**Lemma 1 (BFS is complete and finds the shortest trace within the bound).** If some $(s, a) \in E^i_k$ gives a transition that violates $\varphi$, BFS returns a trace of minimal length that ends in such a transition.

*Sketch.* BFS expands each state of $\mathrm{Reach}^i_{<k}$ exactly once, the first time it is reached, and evaluates $\varphi$ on every successor in scope. Visiting a state only once loses nothing. $\mathit{step}$ is deterministic, so the transition from $(s, a)$ does not depend on the path to $s$, and $\varphi$ sees only $(\mathit{observe}(s), a, o, \mathit{observe}(s'))$. Expanding level by level gives minimal length. ∎

Two details of the implementation matter for what "checked" means:

- **Transitions that leave the scope are not judged.** If $\mathit{step}(s, a)$ falls outside $B_i$, that transition is skipped before $\varphi$ is evaluated. A violation that leaves the scope in its last step is therefore missed, even within depth $k$.
- **Each property runs its own BFS.** The cost is the number of properties times the area's states. The areas are small enough that this has not mattered so far: at most 1,713 states at depth 8, under 0.1 s each.

Default $k = 8$ (`search_depth`).

### 4.2 Situations as probes

For each active entry with a situation, the ledger generates a **probe** property $\neg\psi_e$, named `<id>?reach` (`ledger.render`). BFS's counterexample to the probe is a shortest trace that reaches the situation. Then

$$\mathrm{reached}(e) \iff \exists i.\ \mathrm{BFS}(M, \Sigma_i, B_i, \neg\psi_e, k) \neq \text{none}.$$

If the situation is not reached, the agent is asked once, for each version of $\psi_e$, to widen the areas (`situations-asked.json`).

## 5. Detection and confirmation on the code

A violation is reported only after it has been seen on the real code. It can be found in two ways (`companion/check.py`):

1. **Directly, on validation's real runs.** For $w \in T_t$ and a step $i$ of $\rho_P(w)$, $\neg\varphi_e(\tau^P_i)$. To save program runs, validation may read the real state only where the model's state changes. A violation found this way is therefore confirmed by running the trace again and reading the state after every step.
2. **By search.** BFS returns a trace $w$ for $\varphi_e$. The trace is run on the code, reading the state after every step. It counts if $\neg\varphi_e$ holds on some transition of $\rho_P(w)$.

If a search counterexample does not reproduce, the model is wrong at $w$. The trace joins $X$, the entry is unchecked for this turn, and, with an agent, the model is validated and repaired again (at most one more pass of the whole check).

**Proposition 1 (every reported violation is real, relative to the adapter).** If the companion reports $e$ violated with trace $w$, then running the concrete trace $\gamma^*(w)$ on $P_t$ produces reads and outputs whose abstraction violates $\varphi_e$.

*Sketch.* Both channels end in a run on $P_t$ that observes every step, and $\varphi_e$ is evaluated on the abstracted transitions of that run (the `eval` subcommand). The model is not trusted here. ∎

What Proposition 1 does **not** say:

- that $\varphi_e$ means what the developer said (that is RQ1);
- that $\alpha$ is faithful. A wrong $\alpha_\Theta$ can invent a violation. The adapter is trusted, and the trusted base is $D$, A1 and Lean's evaluator.

Under those two caveats, the companion gives no false alarms by construction. This has a consequence for RQ2 (§8.3).

**Proposition 2 (relative completeness of bounded checking).** Suppose $M \models_D w$ for every trace $w$ of area $i$ up to length $k$ (inside $B_i$). If some such trace makes the program violate $\varphi_e$, the companion reports $e$ violated, with a trace no longer than the shortest one.

*Sketch.* By conformance, the program's violating run equals the model's run. By Lemma 1, BFS finds a shortest violating $w$. Replaying $w$ on the code gives the same transitions, again by conformance, so the violation reproduces. ∎

The hypothesis of Proposition 2 is conformance on **every** trace of the area up to length $k$, but only $T_t$ is validated. The following lemma connects the two. It needs one more assumption.

**Assumption A2 (the abstract state is enough).** If two abstract traces $w, w'$ lead the model to the same state, and the program conforms on both, then for every action $a$ the program's last step on $w \cdot a$ and on $w' \cdot a$ abstracts to the same output and observation.

A2 says that $\alpha$ keeps everything the program's future behavior depends on. design.md §6 ("when α loses too much") describes how it fails: the same abstract trace behaves differently for different concrete values.

**Lemma 2 (transition coverage validates every trace up to depth d+1).** Assume A2. If $M \models_D \mathrm{Cov}_d$, then $M \models_D w$ for every trace $w$ of each area with $|w| \le d + 1$.

*Sketch.* By induction on $|w|$. Write $w = u \cdot a$ with $|u| \le d$. By the induction hypothesis $M \models_D u$, and $u$ leads the model to some $s \in \mathrm{Reach}_{\le d}$. The covered trace $\pi(s) \cdot a$ conforms, and so does its prefix $\pi(s)$. By A2, the program's step $a$ after $u$ abstracts the same way as after $\pi(s)$, which is the model's step from $s$. ∎

**Corollary.** With the defaults $d = 4$ and $k = 8$, Proposition 2 is backed by validation only up to depth 5. Steps 6 to 8 of the search rely on random traces and on A2 extending further. They are covered as well if the area's reachable set has stopped growing by depth 4 ($\mathrm{Reach}_{\le 4} = \mathrm{Reach}_{\le 8}$), because then $\mathrm{Cov}_4$ already covers every transition the search examines. Whether forge's areas stop growing that early has not been measured (§9, item 1).

**Proposition 3 (proofs, design only).** If Lean proves that every action preserves $\varphi$ on the model, and $M \models_D A^*$, then every reachable state of the program satisfies $\varphi$, for traces of any length. In practice only $M \models_D T_t$ is established, so "proved" means: true of the model for traces of any length, and the model conforms on $T_t$. The forge model has 7 hand-written proofs (`lean/Forge/Proofs.lean`), but no proof attempts run in the per-turn pipeline.

## 6. Evidence levels

These are the levels a report can give, and what each one guarantees. The conditions are evaluated in the order listed.

| Level | Condition | What it guarantees | In the code |
| --- | --- | --- | --- |
| `unchecked(unformalized)` | $\varphi_e$ or $\psi_e$ does not typecheck | nothing | yes |
| `violated(w, channel)` | $\neg\varphi_e$ on a transition of $\rho_P(w)$ | Proposition 1 | yes, as `violated`, with the commands and whether $e$ was stated this turn |
| `unchecked(model broken)` | $M$ does not build, or the program does not conform to $M$ on $T_t$ after repair | nothing | yes |
| `unchecked(model disproved)` | BFS found a counterexample that does not reproduce | nothing, this turn | yes |
| `unchecked(situation not reached)` | $\psi_e$ is given and not reached | nothing | yes |
| `bounded(k, areas, T)` | none of the above. No violation in $E^i_k$ for any area, none on $T_t$, and $\psi_e$ reached (if given) | Proposition 2, valid up to depth $d+1$ (Lemma 2) under A1 and A2, inside the areas' scopes | yes, called `checked`, with $k$, the areas and $\lvert T\rvert$. A remaining non-empty $\mathrm{Gap}_t$ is mentioned in the detail. |
| `proved` | Lean proof of preservation, and $M \models_D T_t$ | Proposition 3 | no |
| `unobservable` | $\alpha$ cannot express the state $\varphi_e$ refers to | — | no |

Two points about `bounded`, which the code calls `checked`, need care:

- **"Situation reached" means at least one witness, not coverage.** An entry with a vague $\psi_e$ is reached easily, and the level then overstates what was searched.
- **An entry without $\psi_e$ is reported `checked` with no witness at all.** Today that happens whenever the extractor leaves the situation out.

## 7. The per-turn algorithm

The parameters are those in `apps/forge/companion.json` and the code's defaults.

```
Turn(L, m, P):                                    # Stop hook; skipped if stop_hook_active
  L ← tc(apply(L, E(L, m, V(M))))                   # §2.3; only the developer's messages m
  for attempt in 1..2:
     (M, D, T, V, Gap) ← Sync(M, D, P, L)          # §3
     Viol ← confirmed violations on V's real runs   # §5, channel 1
     if program conforms to M on T:
        for area i, entry e active:
           w ← BFS(M, Σ_i, B_i, φ_e, k=8)          # §4.1
           if w and e ∉ Viol or w is shorter:
              if ¬φ_e on ρ_P(w): Viol[e] ← w        # §5, channel 2
              else: X ← X ∪ {w}; disproved ∪= {e}
           reached[e] ← BFS(M, Σ_i, B_i, ¬ψ_e, k) ≠ none for some i
     U ← {e : ψ_e given, not reached[e], not violated, not asked about this ψ_e yet}
     if U ≠ ∅ and attempt = 1: repair, asking to widen the areas for U
     if (X grew or U ≠ ∅) and attempt = 1: continue          # Sync again: X now mismatches
     break
  R ← evidence for each entry (§6)
  F ← at most 3 violated entries, each with the developer's words, its turn and the reproduction
  if F ≠ ∅: block the agent's stop with F (once per turn)

Sync(M, D, P, L):
  build M with L's properties; T ← Cov_4(M) ∪ Rnd_200 ∪ X
  V ← validate(T)                                   # cached by the code each trace ran (§3.4)
  Gap ← Δ(P_b, P) \ Lines(T)
  if mismatch or Gap ≠ ∅: up to 2 LLM repair rounds; revert a model that does not build
  if the program conforms to M on T and (Gap = ∅ or a repair was attempted): P_b ← P
```

`synthesize` is `Sync` started from an empty model. Its task is `companion/prompts/synthesize.md`.

## 8. Comparison with report 1

### 8.1 Stage by stage

| Report 1 says (§3.1) | What the code does | Difference |
| --- | --- | --- |
| The companion "will listen to the developer-agent conversation and formalize the constraints the developer and agent agree upon". | Extraction reads only the developer's messages ($m_t$, from `UserPromptSubmit`). | **Not met.** A rule the agent proposes and the developer accepts ("ok, do that") is never seen. The `Stop` event's `transcript_path` is available but unused. |
| (1) Constraints become "Lean invariants". | Single-step predicates on transitions, with a situation, statuses and provenance; typechecked, with one repair round; add, revise and retire. | **More than stated.** Report 2 should say "properties of single transitions", which include invariants, and should state the one-step limit (§2.1). |
| (1) "These invariants are confirmed by the developer through examples." | `confidence` is recorded and never used. No questions are asked. | **Not built.** It is in Figure 1 ("confirm by example") and in RQ1's metric "with and without developer confirmation". |
| (2) "An LLM updates a Lean model". | An LLM agent writes the model **and the adapter** (`synthesize`) and repairs both. | **More than stated.** The report says nothing about who writes the "test harness". The adapter is LLM-written and trusted (Proposition 1). |
| (2) Trace validation compares states; "the LLM then repairs the model if the resulting states differ". | Compares outputs and observations at every step (Definition 1). Traces by transition coverage, random, and disproved counterexamples (§3.1). Repair is also triggered by changed code no trace reaches (§3.2) and by unreached situations (§4.2). Two rounds; a model that does not build is reverted. | **More than stated.** None of trace selection, change impact or the repair budget is in the report. These are the parts that made the prototype work: random traces missed 4 of 10 seeded bugs. |
| (3) "Bounded model checking, exhaustively checking every action sequence up to a fixed length". | BFS per area, over that area's alphabet, inside its scope bound, to depth 8 (§4.1). | **Overstated.** Sequences that mix areas, actions outside the alphabets, and transitions leaving the scope are not checked. Report 2 should name the areas and scopes, and cite the small-scope hypothesis. |
| (3) — | Situations as probes (§4.2). | **Not in the report.** It closed the hole where two violated constraints were reported checked (synthesis-and-repair.md). |
| (4) The counterexample is turned "into a test on the code. If that test fails, then the result is returned to the coding agent." | The counterexample is replayed through the adapter (§5, channel 2). Violations are also found directly on validation's real runs (channel 1). A counterexample that does not reproduce becomes a validation trace and triggers repair. At most 3 per turn, with one retry. | **Met, and more.** The report does not say what happens when the test passes. Reproduced traces are not kept as regression tests, although design.md §1 says they are. |
| Figure 1: one loop | The loop as in §7 | Figure 1 has no arrow for repair after a counterexample the code does not reproduce, nor for change impact. |
| — | Evidence levels: `checked`, `violated`, `unchecked` with a reason (§6) | **Not in the report**, yet it is the main answer to the question the report asks: "how can we gain confidence...". `proved` and `unobservable` are design only. |
| — | Triage: violations of entries stated this turn are labeled "not yet met", earlier ones "regression". | **Not in the report.** Telling a regression from a change of mind (design.md §5) is not built. |

### 8.2 The evaluation (report 1, §4)

| Report 1 | Status |
| --- | --- |
| Stateful subset of SlopCodeBench | Only forge has a model: hand-built and LLM-written. Nothing yet for dynamic_config_service_api or trajectory_api. |
| Subset of EvoCode-Bench | Nothing run. The tasks need Go, Docker and Harbor, none of which is installed. Only the preliminary study used its published results. |
| Simulated developer | Not built. |
| Mutation operators: drop a guard, update before validation, off-by-one, change of output format | Forge has 10 mutants: 4 drop a guard, 2 update before validating, 1 changes the output format, and 3 are other kinds (2 forget to update derived state, 1 is a wrong multiplicity). **No off-by-one mutant.** No mutants for any other task. |
| RQ2: BMC against PBT, same invariants, same time budget | Not run. The PBT side-car is not built. The feasibility study compared random traces with transition coverage, and search with hand-built variant models. Neither is the RQ2 comparison. |
| Configurations: agent-only, reminder, PBT side-car, companion, oracle gate | Only the companion is built. Agent-only is the companion switched off. The reminder is a small hook. The PBT side-car and the oracle gate need work. |
| One retry per checkpoint | One retry per **turn** (`stop_hook_active`). A checkpoint spans several turns once the simulated developer splits it, so the two differ. Report 2 should say which one is meant. |
| RQ1 metrics: precision and recall, typecheck rate, updated or retired constraints | Needs ground-truth annotations, which do not exist. The typecheck rate can be read from the ledger (unformalized entries). |
| RQ4 metrics: time, cost, tokens, failure rate | Time and dollar cost are recorded for synthesis and repair, and time and program runs for validation. Tokens, and the cost of extraction, are not recorded. |
| 3 runs per configuration | No runs yet. |

### 8.3 What the formalization changes in the evaluation

- **RQ2 precision is 1 by construction for both checkers.** With ground-truth invariants, the companion reports only violations reproduced on the code (Proposition 1). The PBT side-car also runs on the code, through the same adapter. Neither can raise a false alarm except through an adapter error, so "precision on the unmodified references" measures the adapter, not the checking method. RQ2 should lead with recall, turns-to-detection, time-to-detection and counterexample length. Precision is still reported, as a check on the adapter.
- **RQ2 is a trade-off between search coverage and conformance.** PBT runs on the real code, so it has no conformance gap, but it samples. BMC is exhaustive within $(\Sigma_i, B_i, k)$, but only as far as Proposition 2's hypothesis holds. Stated precisely, RQ2 asks whether exhaustive search finds more than the conformance gap costs. Escapes (a constraint reported checked that the oracle finds violated) measure that gap. That is why recording escapes (evaluation.md §9) belongs in the RQ2 analysis, not only in the tool's own checks.
- **"Same time budget" needs a definition.** Search itself takes under 0.1 s per area. The companion's cost is validation (about 3 s per turn when cached, 20 s cold on forge) plus LLM repair (tens of seconds). A budget that counts only search time favors the companion, and one that includes LLM time has no counterpart in PBT. A fair choice: give both checkers the same wall-clock budget for running the program, with validation and replay counted for the companion and test runs for PBT. Report LLM time separately, under RQ4.
- **RQ1 equivalence across two vocabularies.** evaluation.md §4 calls an extracted predicate correct if it agrees with the reference predicate on every state the reference model reaches. But extracted predicates are written against the **LLM-written** model's types, not the reference model's, so the two cannot be evaluated on the same transitions. There are two ways out:
  1. Run RQ1 extraction twice: once against the reference model's vocabulary, which isolates extraction and allows the mechanical check, and once against the synthesized model, which measures the whole pipeline.
  2. Judge equivalence by behavior on the code: both predicates must flag the same mutants and the same oracle traces.

  We recommend doing both.

### 8.4 Wording for report 2

| Report 1 | Accurate |
| --- | --- |
| "Lean invariants" | "properties of single transitions (state invariants and rules about one action), as Lean predicates" |
| "checks every short sequence of actions" | "checks every sequence of up to $k$ actions within each search area's alphabet and scope" |
| "test harness" | "adapter" (the name used in the code and docs) |
| "the LLM repairs the model if the resulting states differ" | "...if outputs or states differ on some validation trace, or if changed code is run by no trace" |
| "returns failed checks to the agent" | "returns violations reproduced on the real program" |
| "confirmed by the developer through examples" | keep as planned work, not as part of the technique, until it is built |

Two related works that the approach relies on are cited in the brief but not in report 1: Cedar (the standard for checking a Lean model against code by differential testing) and the small-scope hypothesis (Alloy, SmallCheck).

## 9. What to do next

In order of value for report 2. Each item is small.

1. **Validate as deep as the search goes, or measure.** Log $\lvert\mathrm{Reach}_{\le 4}\rvert$ and $\lvert\mathrm{Reach}_{\le 8}\rvert$ per area. If they differ, either raise `transition_depth` for small areas or report `checked` to depth 5 (Lemma 2). This costs one extra `paths` call per area.
2. **Check W1 (every violation is in its situation).** Add a second probe per entry, $\varphi_e \lor \psi_e$. A counterexample is a violation outside the stated situation, which means the situation is too narrow. The search is the same as for the probes that exist.
3. **Count situation witnesses.** Report how many examined transitions satisfy $\psi_e$, not only whether one does, so that a vague situation is visible. An entry without $\psi_e$ should be labeled `checked (no situation)`.
4. **Judge transitions that leave the scope.** Evaluate $\varphi$ before pruning on $B_i$ in `bfs`. It is a two-line change, and a violation whose last step leaves the scope is then still found. States outside the scope are still not expanded.
5. **Read the agent's side of the conversation.** Pass the turn's assistant messages, from `transcript_path`, to extraction, marked as the agent's proposals. That meets report 1's "constraints the developer and agent agree upon".
6. **Rename the evidence level** `checked` to `bounded`, and show $k$, the areas and $d$ in the report.
7. **Fix the RQ2 and RQ1 protocols** as in §8.3, before the PBT side-car is built, so that both side-cars share one budget definition.
8. **Record tokens** from the headless runs (`usage`), and time and cost for extraction (RQ4).
9. **Add an off-by-one mutant** to forge, so that all four of report 1's operators are represented.
