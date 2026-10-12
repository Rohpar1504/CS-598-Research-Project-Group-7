"""Summarize the labeled sample: shares of regressions by kind and by manifestation,
per requirement and weighted by the number of regression events. Also reports how many
regression events fall in rounds that EvoCode-Bench labels as correction or conflict."""

import csv
import math
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sample = {(r["task_name"], r["req"]): r for r in csv.DictReader(open(HERE / "sample.csv"))}
labels = list(csv.DictReader(open(HERE / "labels.csv")))
regressions = list(csv.DictReader(open(HERE / "regressions.csv")))


def share(counter: Counter, total: float) -> str:
    out = []
    for k, v in counter.most_common():
        p = v / total
        # Normal-approximation 95% interval. Only meaningful per requirement: events on the
        # same requirement are correlated, so per-event intervals are too narrow.
        half = 1.96 * math.sqrt(p * (1 - p) / total) if total > 1 else 0
        out.append(f"{k} {p:.0%} (±{half:.0%})")
    return ", ".join(out)


for stratum in ["random", "opus"]:
    rows = [l for l in labels if l["stratum"] == stratum]
    if stratum == "opus":
        # Weight by Opus's own events only, not every model's events on these requirements.
        opus_events = Counter((r["task_name"], r["req"]) for r in regressions if r["model"] == "Opus-4.8-xhigh")
        weights = {(l["task_name"], l["req"]): opus_events[(l["task_name"], l["req"])] for l in rows}
    else:
        weights = {(l["task_name"], l["req"]): int(sample[(l["task_name"], l["req"])]["n_events"]) for l in rows}
    print(f"== {stratum}: {len(rows)} requirements, {sum(weights.values())} regression events")
    for dim in ["kind", "manifestation"]:
        per_req = Counter(l[dim] for l in rows)
        per_event = Counter()
        for l in rows:
            per_event[l[dim]] += weights[(l["task_name"], l["req"])]
        print(f"  {dim:13} per requirement: {share(per_req, len(rows))}")
        print(f"  {dim:13} per event:       {share(per_event, sum(weights.values()))}")
    wrong = [l for l in rows if l["manifestation"] == "WRONG"]
    print(f"  kind among WRONG only ({len(wrong)}): {share(Counter(l['kind'] for l in wrong), len(wrong))}")

import tomllib
tasks_dir = HERE.parent.parent / "data" / "evocodebench" / "evocodebench_wotraj"
labels_per_round = []
for task in tasks_dir.iterdir():
    steps = tomllib.loads((task / "task.toml").read_text())["metadata"]["requirement_chain"]["steps"]
    labels_per_round += [s.get("change_types", []) for s in steps if s["step"] != "round-1"]
base = sum(1 for c in labels_per_round if "correction" in c or "conflict" in c) / len(labels_per_round)
print(f"\nBase rate: {base:.0%} of rounds after round 1 are labeled correction or conflict")
events = len(regressions)
in_change = sum(1 for r in regressions if "correction" in r["change_types"] or "conflict" in r["change_types"])
print(f"\nAll {events} regression events: {in_change} ({in_change/events:.0%}) happen in a round labeled correction or conflict")

# Regression rate normalized by opportunity: of the requirements a model passed at round
# i-1 that round i tests again, how many does it fail at round i?
import json
import sys

sys.path.insert(0, str(HERE))
from extract_regressions import RESULTS, TASKS, registered, round_dirs, tests_text  # noqa: E402

opportunities, regressed = Counter(), Counter()
for result_file in sorted(RESULTS.glob("*.json")):
    task = TASKS / result_file.stem
    regs = {int(r.name.split("-")[1]): set(registered(tests_text(r))) for r in round_dirs(task)}
    for model, rounds in json.loads(result_file.read_text())["models"].items():
        fails = {int(k): {f["req"] for f in v["fails"]} for k, v in rounds.items() if v}
        for i in fails:
            if i - 1 in fails and i - 1 in regs and i in regs:
                passed_before = (regs[i - 1] - fails[i - 1]) & regs[i]
                opportunities[model] += len(passed_before)
                regressed[model] += len(passed_before & fails[i])
print("\nRegression rate (requirements broken / requirements passing and retested), by model:")
for model in sorted(opportunities, key=lambda m: regressed[m] / opportunities[m]):
    print(f"  {model:22} {regressed[model]:4} / {opportunities[model]:5} = {regressed[model] / opportunities[model]:.1%}")
