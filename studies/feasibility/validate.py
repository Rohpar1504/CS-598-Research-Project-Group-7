"""Trace-validate the forge model (lean/Forge) against forge's checkpoint 8
reference solution, or against seeded-bug versions of it.

Traces: shortest paths to reachable states in each search area, plus random traces over
the wide validation alphabet.

With --transitions, traces instead cover every transition of the search areas: a
shortest path to each reachable state (up to --depth), extended by every action of that
area's alphabet.

Usage: python -m studies.feasibility.validate [--checkpoints 7 8] [--mutants [NAME ...]]
                                                        [--transitions --depth 4]
Writes studies/feasibility/results/validate_<target>.json and prints a summary.
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from companion.adapters.forge import ForgeAdapter
from companion.lean import LeanModel
from companion.trace_validation import action_kind, random_traces, validate

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "lean" / ".lake" / "build" / "bin" / "forge-model"
SOLUTIONS = ROOT / "data" / "scb-problems" / "forge" / "solutions"
MUTANTS = ROOT / "data" / "forge-mutants"
RESULTS = Path(__file__).parent / "results"
AREAS = ["alloc", "unit", "module", "access"]


def diff_paths(a, b, path="") -> list[str]:
    """Paths at which two JSON values differ (the first few)."""
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [f"{path}{{{','.join(sorted(a))}}} vs {{{','.join(sorted(b))}}}"]
        return [p for k in a for p in diff_paths(a[k], b[k], f"{path}.{k}")][:3]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path} length {len(a)} vs {len(b)}"]
        return [p for x, y in zip(a, b) for p in diff_paths(x, y, f"{path}[]")][:3]
    return [] if a == b else [f"{path}: {json.dumps(a)[:40]} vs {json.dumps(b)[:40]}"]


def cmd_kind(action) -> str:
    return action_kind(action["cmd"])


def signature(m: dict) -> str:
    if m["step"] == -1:
        return "initial observation: " + "; ".join(diff_paths(m["expected_obs"], m["real_obs"]))
    kind = cmd_kind(m["action"])
    d = diff_paths(m["expected_out"], m["real_out"])
    if d:
        return f"{kind} output: " + "; ".join(d)
    return f"{kind} observation: " + "; ".join(diff_paths(m["expected_obs"], m["real_obs"]))


def traces_for(model: LeanModel, n_paths: int, n_random: int, seed: int) -> list:
    traces = []
    for area in AREAS:
        traces += model.paths(area, 6, n_paths // len(AREAS))
    traces += random_traces(model.alphabet("validation"), n_random, 5, 20, seed, kind=cmd_kind)
    return traces


def transition_traces(model: LeanModel, depth: int) -> list:
    traces = []
    for area in AREAS:
        alphabet = model.alphabet(area)
        for path in [[]] + model.paths(area, depth, 10**6):
            traces += [path + [a] for a in alphabet]
    return traces


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoints", type=int, nargs="+", default=[8])
    ap.add_argument("--mutants", nargs="*", default=None, help="all mutants, or the named ones")
    ap.add_argument("--transitions", action="store_true")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--paths", type=int, default=200)
    ap.add_argument("--random", type=int, default=400)
    ap.add_argument("--seed", type=int, default=598)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    model = LeanModel(EXE)
    traces = (transition_traces(model, args.depth) if args.transitions
              else traces_for(model, args.paths, args.random, args.seed))
    tag = f"_transitions{args.depth}" if args.transitions else ""
    RESULTS.mkdir(exist_ok=True)
    if args.mutants is not None:
        names = args.mutants or [d.name for d in sorted(MUTANTS.iterdir())]
        targets = [(f"mutant_{n}", MUTANTS / n / "forge") for n in names]
    else:
        targets = [(f"cp{cp}", SOLUTIONS / f"checkpoint_{cp}" / "forge") for cp in args.checkpoints]

    for label, program in targets:
        start = time.time()
        results = validate(lambda: ForgeAdapter(program), model, traces, args.workers)
        elapsed = time.time() - start
        mismatched = [r for r in results if r.first_mismatch]
        sigs = Counter(signature(r.first_mismatch) for r in mismatched)
        violated = Counter(n for r in results for _, names in r.violations for n in names)
        summary = {
            "target": label, "traces": len(results), "steps": sum(len(r.trace) for r in results),
            "seconds": round(elapsed, 1), "traces_with_mismatch": len(mismatched),
            "mismatch_kinds": sigs.most_common(), "property_violations_on_code": violated.most_common(),
            "examples": [{"trace": r.trace[: r.first_mismatch["step"] + 1], **r.first_mismatch} for r in mismatched[:5]],
        }
        (RESULTS / f"validate_{label}{tag}.json").write_text(json.dumps(summary, indent=1))
        print(f"{label}: {summary['traces']} traces, {summary['steps']} steps, {elapsed:.0f}s, "
              f"{len(mismatched)} traces mismatch")
        for s, n in sigs.most_common(8):
            print(f"   {n:4}  {s[:160]}")
        for s, n in violated.most_common():
            print(f"   property violated on code {n}x: {s}")


if __name__ == "__main__":
    main()
