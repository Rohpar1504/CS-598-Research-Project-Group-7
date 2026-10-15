"""Trace validation of your model against the program. Run: python3 validate.py [--quick]

It builds lean/ (app-model), picks traces that cover every transition of your search areas
plus random traces over your validation alphabet, runs each on the real program through
adapter.py and on your model, and compares outputs and observations step by step.
Done means: it builds, and it reports 0 mismatching traces.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from companion.lean import LeanModel                                   # noqa: E402
from companion.trace_validation import random_traces, transition_traces, validate   # noqa: E402
from adapter import Adapter                                            # noqa: E402

PROGRAM = HERE / "program" / "forge"


def main() -> int:
    quick = "--quick" in sys.argv
    build = subprocess.run(["lake", "build"], cwd=HERE / "lean", capture_output=True, text=True)
    if build.returncode != 0:
        print("lake build failed:\n" + (build.stdout + build.stderr)[-4000:])
        return 1
    model = LeanModel(HERE / "lean" / ".lake" / "build" / "bin" / "app-model")
    traces = transition_traces(model, model.areas(), 2 if quick else 3)
    traces += random_traces(model.alphabet("validation"), 40 if quick else 150, 3, 15, 1)
    results = validate(lambda: Adapter(PROGRAM), model, traces, workers=8)
    bad = [r for r in results if r.first_mismatch]
    print(f"{len(results)} traces, {sum(len(r.trace) for r in results)} steps, {len(bad)} mismatching")
    kinds = Counter()
    for r in bad:
        m = r.first_mismatch
        a = m.get("action")
        kinds[json.dumps(a)[:60] if a else "initial observation"] += 1
    for r in bad[:4]:
        m = r.first_mismatch
        print("\n--- first mismatch of a trace ---")
        print("trace:", json.dumps(r.trace[: m["step"] + 1])[:800])
        for k in ("expected_out", "real_out", "expected_obs", "real_obs"):
            if k in m:
                print(f"{k}: {json.dumps(m[k])[:600]}")
    if bad:
        print("\nmismatches by action:", kinds.most_common(10))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
