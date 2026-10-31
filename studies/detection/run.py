"""Study A (RQ2): which seeded bugs does each checker catch within the same time budget?

Cases: forge's checkpoint 8 reference solution (no bug) and the ten seeded bugs from
studies/feasibility/make_mutants.py (in data/forge-mutants/).

Both checkers are judged by the same 14 specification properties of the Lean model
(lean/Forge/Props.lean), evaluated on what the real program did. The two "[extra]"
properties are not in forge's specification and are ignored.

Checkers:
- lean: for the three bugs that have a model that follows them (lean/Forge/Variants.lean),
  first the model's own bounded search, with each counterexample replayed on the program.
  Then traces chosen from the Lean model's state space, shortest first (transition coverage
  at depth 0, 1, 2, ...), run on the program.
- pbt: property-based testing with Hypothesis. Random traces of 1-20 actions (action kind
  picked uniformly first), run on the program, properties checked after every step,
  failures shrunk. Runs in as many processes as the lean checker, each with its own seeds.
  --pbt-actions areas (default) draws from the same actions as the lean checker, so only
  the search differs; wide draws from the validation alphabet (611 actions).

Simplification: the companion would keep a model that follows the changed code. Only three
bugs have one, so for the other seven the reference model only chooses which traces to run.

Usage: python -m studies.detection.run [--budget 30] [--checkers lean pbt] [--cases NAME ...]
                                       [--workers N]
Writes studies/detection/results/<checker>_<budget>s.json and prints a table.
"""

import argparse
import json
import multiprocessing
import time
from collections import defaultdict
from pathlib import Path

from hypothesis import HealthCheck, Phase, given, seed, settings, strategies as st

from companion.abstraction import expressible
from companion.adapters.forge import ForgeAdapter
from companion.lean import LeanModel
from companion.trace_validation import (action_kind, default_workers, evaluate, transition_traces,
                                        validate, validate_trace)

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "lean" / ".lake" / "build" / "bin" / "forge-model"
REFERENCE = ROOT / "data" / "scb-problems" / "forge" / "solutions" / "checkpoint_8" / "forge"
MUTANTS = ROOT / "data" / "forge-mutants"
RESULTS = Path(__file__).parent / "results"
AREAS = ["alloc", "unit", "module", "access"]
MAX_DEPTH = 8
CHUNK = 50         # traces run between time-budget checks
PBT_MAX_LEN = 20   # actions per random trace
PBT_ROUND = 20     # examples per Hypothesis run; seeds change between runs

# The specification property each seeded bug was written to break (docs/feasibility-study.md).
INTENDED = {
    "reference": None,
    "bind-no-duplicate-guard": "a bind that adds to an assignment succeeds only if the assignment has no bound allocations",
    "bind-not-atomic": "a rejected request leaves the state unchanged",
    "delete-names-partial": "a rejected request leaves the state unchanged",
    "flash-any-image": "a flash succeeds only with an image some module already had",
    "module-not-linked": "a unit lists exactly the modules attached to it",
    "remove-keeps-assignment": "an allocation is bound exactly when it has an assignment",
    "system-scope-allowed": "blueprint create and delete never succeed in system scope",
    "tag-duplicate-allowed": "no module has two tags with the same key",
    "tenant-always-visible": "tenant_id is shown only at revision 1.1 or higher",
    "typea-not-doubled": "allocation create yields two allocations per TYPE_A requirement set and one per other set",
}
VARIANTS = {"bind-no-duplicate-guard", "delete-names-partial", "flash-any-image"}


def program_for(case: str) -> Path:
    return REFERENCE if case == "reference" else MUTANTS / case / "forge"


def spec_violations(result) -> list[tuple[int, str]]:
    """(step, property) for each specification property broken on the program."""
    return [(step, n) for step, names in result.violations for n in names if not n.startswith("[extra]")]


def first_finding(results, start: float, how: str) -> dict | None:
    """The shortest violating trace among `results`, if any."""
    hits = [(step, prop, r.trace) for r in results for step, prop in spec_violations(r)]
    if not hits:
        return None
    step, prop, trace = min(hits, key=lambda h: h[0])
    return {"property": prop, "how": how, "seconds": round(time.time() - start, 1),
            "steps": step + 1, "trace": [a["cmd"] for a in trace[: step + 1]]}


def lean_check(case: str, budget: float, workers: int) -> dict:
    program = program_for(case)
    start = time.time()
    out = {"found": None, "traces_run": 0, "depth_reached": None}

    # 1. Bounded search of a model that follows the bug, counterexamples replayed on the code.
    if case in VARIANTS:
        model = LeanModel(EXE, variant=case)
        adapter = ForgeAdapter(program)
        try:
            for area in AREAS:
                for r in model.check(area, MAX_DEPTH):
                    if r["counterexample"] and not r["property"].startswith("[extra]"):
                        found = first_finding([validate_trace(adapter, model, r["counterexample"])],
                                              start, "model search")
                        if found:
                            out["found"] = found
                            return out
        finally:
            adapter.close()

    # 2. Traces chosen from the reference model, shortest first, run on the code.
    model = LeanModel(EXE)
    seen = set()
    for depth in range(MAX_DEPTH + 1):
        new = []
        for t in transition_traces(model, AREAS, depth):
            key = json.dumps(t, sort_keys=True)
            if key not in seen:
                seen.add(key)
                new.append(t)
        for i in range(0, len(new), CHUNK):
            if time.time() - start > budget:
                return out
            chunk = new[i:i + CHUNK]
            results = validate(lambda: ForgeAdapter(program), model, chunk, workers)
            out["traces_run"] += len(chunk)
            out["depth_reached"] = depth
            found = first_finding(results, start, "model-chosen traces")
            if found and found["seconds"] <= budget:
                out["found"] = found
                return out
    return out


def _pbt_worker(job) -> dict:
    """One process of the PBT checker: Hypothesis runs with fresh seeds until it finds a
    violation or the budget is used up."""
    case, budget, worker, start, actions = job
    adapter, model = ForgeAdapter(program_for(case)), LeanModel(EXE)
    alphabet = (model.alphabet("validation") if actions == "wide"
                else [a for area in AREAS for a in model.alphabet(area)])
    alphabet = list({json.dumps(a, sort_keys=True): a for a in alphabet}.values())  # areas share some actions
    by_kind = defaultdict(list)
    for a in alphabet:
        by_kind[action_kind(a["cmd"])].append(a)
    kinds = sorted(by_kind)
    action = st.sampled_from(kinds).flatmap(lambda k: st.sampled_from(by_kind[k]))
    found, examples = [], 0

    def check_trace(trace):
        nonlocal examples
        if time.time() - start > budget:
            return  # out of time: remaining examples are no-ops
        examples += 1
        adapter.reset()
        before = adapter.observe()
        for i, a in enumerate(trace):
            out = adapter.run(a)
            after = adapter.observe()
            t = {"before": before, "action": a, "out": out, "after": after}
            if expressible(t):
                broken = [n for n in evaluate(model, [t])[0] if not n.startswith("[extra]")]
                if broken:
                    found.append({"property": broken[0], "how": "pbt",
                                  "seconds": round(time.time() - start, 1), "steps": i + 1,
                                  "trace": [x["cmd"] for x in trace[:i + 1]]})
                    raise AssertionError(broken)
            before = after

    rnd = 0
    while not found and time.time() - start < budget:
        test = seed(worker * 10_000 + rnd)(settings(
            max_examples=PBT_ROUND, deadline=None, database=None,
            suppress_health_check=list(HealthCheck), phases=[Phase.generate, Phase.shrink],
        )(given(st.lists(action, min_size=1, max_size=PBT_MAX_LEN))(check_trace)))
        try:
            test()
        except Exception:
            pass  # a failure (found is set), or shrinking cut off by the budget
        rnd += 1
    adapter.close()
    return {"examples": examples, "found": found}


def pbt_check(case: str, budget: float, workers: int, actions: str = "areas") -> dict:
    start = time.time()
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        parts = pool.map(_pbt_worker, [(case, budget, w, start, actions) for w in range(workers)])
    found = [f for p in parts for f in p["found"] if f["seconds"] <= budget]
    first = min(found, key=lambda f: f["seconds"]) if found else None
    if first:
        first["shortest_steps"] = min(f["steps"] for f in found)  # after shrinking
    return {"found": first, "examples": sum(p["examples"] for p in parts)}


CHECKERS = {"lean": lean_check, "pbt": pbt_check}


def verdict(case: str, found: dict | None) -> str:
    if case == "reference":
        return "false alarm" if found else "clean"
    if not found:
        return "missed"
    return "caught" if found["property"] == INTENDED[case] else "caught (other property)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=30, help="seconds per case per checker")
    ap.add_argument("--checkers", nargs="+", choices=list(CHECKERS), default=list(CHECKERS))
    ap.add_argument("--cases", nargs="*", default=list(INTENDED))
    ap.add_argument("--workers", type=int, default=default_workers())
    ap.add_argument("--pbt-actions", choices=["areas", "wide"], default="areas",
                    help="areas: the lean checker's actions (about 50); wide: the validation alphabet (611)")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    for name in args.checkers:
        label = f"pbt-{args.pbt_actions}" if name == "pbt" else name
        print(f"== {label}, {args.budget:g} s per case, {args.workers} workers")
        rows = []
        for case in args.cases:
            if name == "pbt":
                res = pbt_check(case, args.budget, args.workers, args.pbt_actions)
            else:
                res = lean_check(case, args.budget, args.workers)
            row = {"case": case, "intended": INTENDED[case], "verdict": verdict(case, res["found"]), **res}
            rows.append(row)
            f = res["found"]
            if f:
                detail = f"{f['seconds']:6.1f}s {f['steps']:2} steps  {f['how']}"
                if "shortest_steps" in f:
                    detail += f", shrunk to {f['shortest_steps']}"
            elif name == "lean":
                detail = f"{res['traces_run']} traces, depth {res['depth_reached']}"
            else:
                detail = f"{res['examples']} random traces"
            print(f"{case:26} {row['verdict']:24} {detail}")

        out = RESULTS / f"{label}_{args.budget:g}s.json"
        out.write_text(json.dumps({"checker": label, "budget": args.budget, "workers": args.workers,
                                   "cases": rows}, indent=1))
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
