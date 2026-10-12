"""Find regressions in EvoCode-Bench's published v2 results.

A regression is a requirement that a model passed at round i-1 and fails at round i,
where round i-1's cumulative tests already covered it. A requirement that fails in the
round that introduces it is a new-requirement failure, not a regression.

The published results list only failing requirements per round. Which requirements a
round's tests cover comes from the `reg "<case>" <origin-round> <req> <type> ...` lines
that every EvoCode-Bench test script uses to register its cases. For the few requirement
ids that no reg line mentions (cases registered dynamically), a word match on the test
files is used instead, and the row is marked coverage_by=text.

Usage: python extract_regressions.py [DATA_DIR]   (default: data/evocodebench)
Writes regressions.csv and new_failures.csv next to this script.
"""

import csv
import json
import re
import shlex
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent.parent / "data" / "evocodebench"
TASKS = DATA / "evocodebench_wotraj"
RESULTS = DATA / "results-repo" / "docs" / "data" / "tasks"


def round_dirs(task: Path) -> list[Path]:
    return sorted((task / "steps").glob("round-*"), key=lambda p: int(p.name.split("-")[1]))


def tests_text(round_dir: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in (round_dir / "tests").rglob("*") if p.is_file())


def registered(text: str) -> dict[str, int]:
    """Requirement id -> round that introduced it, from the reg lines of one test script."""
    reqs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("reg "):
            continue
        try:
            args = shlex.split(line)
        except ValueError:
            continue
        if len(args) >= 4 and args[2].startswith("round-"):
            reqs.setdefault(args[3], int(args[2].split("-")[1]))
    return reqs


def mentions(text: str, req: str) -> bool:
    return re.search(rf"(?<![\w.\-]){re.escape(req)}(?![\w.\-])", text) is not None


def change_types(task: Path) -> dict[int, list[str]]:
    meta = tomllib.loads((task / "task.toml").read_text())
    steps = meta.get("metadata", {}).get("requirement_chain", {}).get("steps", [])
    return {int(s["step"].split("-")[1]): s.get("change_types", []) for s in steps}


def main() -> None:
    regressions, new_failures = [], []
    for result_file in sorted(RESULTS.glob("*.json")):
        task_id = result_file.stem
        task = TASKS / task_id
        published = json.loads(result_file.read_text())
        tests = {int(r.name.split("-")[1]): tests_text(r) for r in round_dirs(task)}
        regs = {i: registered(text) for i, text in tests.items()}
        all_registered = set().union(*regs.values())
        changes = change_types(task)

        for model, rounds in published["models"].items():
            fails = {int(k): {f["req"]: f for f in v["fails"]} for k, v in rounds.items() if v}
            for i in sorted(fails):
                for req, f in fails[i].items():
                    row = {
                        "model": model,
                        "task": task_id,
                        "task_name": published["meta"]["name"],
                        "round": i,
                        "req": req,
                        "type": f.get("type", ""),
                        "n_failed_cases": f.get("n", ""),
                        "change_types": "+".join(changes.get(i, [])),
                        "reason": (f.get("reason") or "")[:300],
                        "intents": " | ".join(sorted({c.get("intent", "") for c in f.get("cases", [])}))[:300],
                    }
                    if req in all_registered:
                        row["coverage_by"] = "reg"
                        row["origin_round"] = regs.get(i, {}).get(req, "")
                        covered_before = req in regs.get(i - 1, {})
                    else:
                        row["coverage_by"] = "text"
                        row["origin_round"] = ""
                        covered_before = i - 1 in tests and mentions(tests[i - 1], req)
                    if not covered_before:
                        new_failures.append(row)
                    elif i - 1 in fails and req not in fails[i - 1]:
                        regressions.append(row)
                    # Otherwise it also failed at i-1: a persisting failure, not a new event.

    for name, rows in [("regressions.csv", regressions), ("new_failures.csv", new_failures)]:
        with open(HERE / name, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    per_model = Counter(r["model"] for r in regressions)
    tasks_per_model = defaultdict(set)
    for r in regressions:
        tasks_per_model[r["model"]].add(r["task"])
    print(f"{len(regressions)} regressions, {len(new_failures)} new-requirement failures")
    for model, n in per_model.most_common():
        print(f"  {model:22} {n:4} regressions in {len(tasks_per_model[model]):2} tasks")


if __name__ == "__main__":
    main()
