"""Draw the labeling sample: every requirement Opus 4.8 regressed on, plus a seeded random
sample of 150 other (task, requirement) pairs from regressions.csv. Writes sample.csv."""

import csv
import random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
rows = list(csv.DictReader(open(HERE / "regressions.csv")))
pairs = defaultdict(list)
for r in rows:
    pairs[(r["task_name"], r["req"])].append(r)

opus = sorted({k for k, rs in pairs.items() if any(r["model"] == "Opus-4.8-xhigh" for r in rs)})
rest = sorted(k for k in pairs if k not in set(opus))
random.seed(598)
sample = [(k, "opus") for k in opus] + [(k, "random") for k in random.sample(rest, 150)]

with open(HERE / "sample.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["task_name", "req", "stratum", "n_events", "n_models", "origin_round", "rounds", "change_types", "intents", "reason"])
    for (task, req), stratum in sample:
        rs = pairs[(task, req)]
        w.writerow([task, req, stratum, len(rs), len({r["model"] for r in rs}), rs[0]["origin_round"],
                    " ".join(sorted({r["round"] for r in rs}, key=int)),
                    " ".join(sorted({r["change_types"] for r in rs})),
                    rs[0]["intents"][:160], rs[0]["reason"][:160]])
print(f"{len(sample)} pairs ({len(opus)} opus, 150 random) out of {len(pairs)}")
