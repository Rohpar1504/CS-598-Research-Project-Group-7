"""The companion's loop on the forge model.

For the unmodified checkpoint 8 code and for three seeded bugs (with the model updated by
hand to follow each, see lean/Forge/Variants.lean):

1. Trace-validate the model against the code (skipped for the reference: see
   validate.py).
2. Search every area of the model for property violations.
3. Replay each counterexample on the real code and evaluate the properties there. Only a
   violation that reproduces on the code would be reported to the agent.

Usage: python -m studies.feasibility.search_and_confirm [--depth 8]
"""

import argparse
import json
import time
from pathlib import Path

from companion.adapters.forge import ForgeAdapter
from companion.lean import LeanModel
from companion.trace_validation import validate, validate_trace
from studies.feasibility.validate import AREAS, traces_for

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "lean" / ".lake" / "build" / "bin" / "forge-model"
REFERENCE = ROOT / "data" / "scb-problems" / "forge" / "solutions" / "checkpoint_8" / "forge"
MUTANTS = ROOT / "data" / "forge-mutants"
RESULTS = Path(__file__).parent / "results"
CASES = [("reference", REFERENCE), ("bind-no-duplicate-guard", None), ("delete-names-partial", None),
         ("flash-any-image", None)]


def check(model: LeanModel, area: str, depth: int) -> list:
    return model._run("check", area, str(depth), model.variant)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=8)
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    report = []
    for name, program in CASES:
        program = program or MUTANTS / name / "forge"
        model = LeanModel(EXE, variant=name)
        entry = {"case": name}
        if name != "reference":
            results = validate(lambda: ForgeAdapter(program), model, traces_for(model, 200, 400, 598))
            entry["validation"] = {"traces": len(results),
                                   "mismatches": sum(1 for r in results if r.first_mismatch)}
        found, entry["search"] = [], {}
        for area in AREAS:
            start = time.time()
            res = check(model, area, args.depth)
            entry["search"][area] = {"seconds": round(time.time() - start, 1),
                                     "states": max(r["statesExplored"] for r in res)}
            found += [(area, r) for r in res if r["counterexample"]]
        adapter = ForgeAdapter(program)
        entry["counterexamples"] = []
        seen = set()
        for area, r in found:
            if r["property"] in seen:
                continue
            seen.add(r["property"])
            replayed = validate_trace(adapter, model, r["counterexample"])
            on_code = sorted({n for _, names in replayed.violations for n in names})
            entry["counterexamples"].append({
                "area": area, "property": r["property"], "steps": len(r["counterexample"]),
                "trace": [a["cmd"] for a in r["counterexample"]],
                "model_matches_code_on_trace": replayed.first_mismatch is None,
                "reproduces": r["property"] in on_code})
        adapter.close()
        report.append(entry)
        print(f"== {name}: " + (f"validation {entry['validation']['mismatches']}/{entry['validation']['traces']} mismatches; "
                                if "validation" in entry else "")
              + "search " + ", ".join(f"{a} {v['states']} states {v['seconds']}s" for a, v in entry["search"].items()))
        for c in entry["counterexamples"]:
            print(f"   {c['property']}: {c['steps']} steps, reproduces on code: {c['reproduces']}")
            print(f"      {json.dumps(c['trace'])[:300]}")
    (RESULTS / "search_and_confirm.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
