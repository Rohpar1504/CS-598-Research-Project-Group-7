"""Synthesis experiment: can an LLM agent, given only the forge app's code, write a Lean model
and an adapter that pass trace validation?

Sets up a fresh workspace outside the repository (so the hand-built model is not at hand),
with the task (template/TASK.md), the generic Lean core, the generic Python runner and the
program, then runs Claude Code headless in it and copies the results to data/synthesis/<run>/.

Usage: python -m studies.synthesis.run_synthesis --workspace DIR --model claude-haiku-5-5 \
           --effort low --budget 10 [--run NAME]
"""

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from companion.llm import find_claude

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).parent / "template"
PROGRAM = ROOT / "data" / "scb-problems" / "forge" / "solutions" / "checkpoint_8"
PROMPT = "Read TASK.md in the current directory and complete the task. Work only inside this directory."


def setup(ws: Path) -> None:
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "lean" / "App").mkdir(parents=True)
    (ws / "program").mkdir()
    (ws / "companion").mkdir()
    shutil.copy(TEMPLATE / "TASK.md", ws)
    shutil.copy(TEMPLATE / "validate.py", ws)
    shutil.copy(TEMPLATE / "lakefile.toml", ws / "lean")
    shutil.copy(ROOT / "lean" / "lean-toolchain", ws / "lean")
    shutil.copytree(ROOT / "lean" / "Companion", ws / "lean" / "Companion")
    for f in ("__init__.py", "lean.py", "trace_validation.py", "runner.py", "abstraction.py"):
        shutil.copy(ROOT / "companion" / f, ws / "companion")
    for f in ("forge", "forge.py"):
        shutil.copy(PROGRAM / f, ws / "program")
    subprocess.run(["lake", "build", "Companion"], cwd=ws / "lean", check=True, capture_output=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--budget", type=float, default=10.0)
    ap.add_argument("--run", default=None)
    args = ap.parse_args()
    run = args.run or f"{args.model}-{args.effort}-{time.strftime('%Y%m%d-%H%M%S')}"
    ws = args.workspace.resolve()
    setup(ws)

    cmd = [find_claude(), "-p", "--model", args.model, "--effort", args.effort,
           "--setting-sources", "", "--permission-mode", "acceptEdits",
           "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep",
           "--output-format", "stream-json", "--verbose", "--no-session-persistence",
           "--max-budget-usd", str(args.budget)]
    start = time.time()
    with open(ws / "transcript.jsonl", "w") as log:
        proc = subprocess.run(cmd, input=PROMPT, cwd=ws, stdout=log, stderr=subprocess.STDOUT, text=True)
    elapsed = time.time() - start

    result = {}
    for line in (ws / "transcript.jsonl").read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event
    out = ROOT / "data" / "synthesis" / run
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ws, out, ignore=shutil.ignore_patterns(".lake", "__pycache__"))
    summary = {"run": run, "model": args.model, "effort": args.effort, "budget_usd": args.budget,
               "exit_code": proc.returncode, "seconds": round(elapsed),
               "cost_usd": result.get("total_cost_usd"), "turns": result.get("num_turns"),
               "stop": result.get("subtype"), "final_message": (result.get("result") or "")[-3000:]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "final_message"}, indent=1))
    print("results in", out)


if __name__ == "__main__":
    main()
