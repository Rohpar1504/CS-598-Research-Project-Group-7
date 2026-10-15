"""Grade a synthesized model and adapter (a workspace from run_synthesis.py), independently
of what the agent reported:

1. Does the Lean model build?
2. Trace validation against forge's checkpoint 8 with more and different traces than the
   agent's validate.py used (transition coverage to depth 4, 400 random traces, new seed).
3. Coverage: which of forge's real commands the adapter ever issues.
4. Seeded bugs (data/forge-mutants): does the model notice each, i.e. mismatch the buggy code?
5. Size of the model and adapter.

Usage: python -m studies.synthesis.grade WORKSPACE
Writes WORKSPACE/grade.json.
"""

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from companion import runner
from companion.lean import LeanModel
from companion.trace_validation import default_workers, random_traces, transition_traces, validate

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "data" / "scb-problems" / "forge" / "solutions" / "checkpoint_8" / "forge"
MUTANTS = ROOT / "data" / "forge-mutants"

# forge's commands at checkpoint 8 (resource, subcommand), from its dispatch code.
COMMANDS = {("version", ""), ("blueprint", "create"), ("blueprint", "list"), ("blueprint", "get"),
            ("blueprint", "delete"), ("allocation", "create"), ("allocation", "list"), ("allocation", "get"),
            ("allocation", "bind"), ("allocation", "delete"), ("unit", "create"), ("unit", "list"),
            ("unit", "get"), ("unit", "activate"), ("unit", "deactivate"), ("ledger", "capabilities"),
            ("module", "create"), ("module", "list"), ("module", "get"), ("module", "flash"),
            ("tag", "create"), ("tag", "list"), ("tag", "get"), ("tag", "delete"), ("tag", "update"),
            ("schema", "init"), ("schema", "version"), ("schema", "upgrade"), ("check", "readiness")}


def load_adapter(ws: Path):
    spec = importlib.util.spec_from_file_location("synth_adapter", ws / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ws))
    spec.loader.exec_module(mod)
    return mod


def code_lines(paths) -> int:
    n = 0
    for p in paths:
        for line in p.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith(("--", "#", "/-", "-/")):
                n += 1
    return n


def command_of(argv: list) -> tuple:
    """(resource, subcommand) of a forge command line, skipping global flags and their values."""
    words, skip = [], False
    for x in map(str, argv):
        if skip:
            skip = False
        elif x.startswith("--"):
            skip = x in ("--data-dir", "--revision", "--scope", "--role", "--rules-file", "--config")
        else:
            words.append(x)
    if not words:
        return "", ""
    return (words[0], "") if words[0] == "version" else (words[0], words[1] if len(words) > 1 else "")


def commands_issued(mod, alphabet: list, program: Path) -> set:
    """Run every validation action once and record which (resource, subcommand) pairs reach
    the program, whether the adapter runs it through companion.runner or (adapters written
    before it) through subprocess.run."""
    seen = set()
    real_run, real_subprocess = runner.run, subprocess.run

    def spy_runner(prog, args, *a, **k):
        seen.add(command_of(args))
        return real_run(prog, args, *a, **k)

    def spy_subprocess(cmd, *a, **k):
        if isinstance(cmd, list) and any(str(x).endswith("forge") for x in cmd):
            i = next(j for j, x in enumerate(cmd) if str(x).endswith("forge"))
            seen.add(command_of(cmd[i + 1:]))
        return real_subprocess(cmd, *a, **k)

    runner.run, subprocess.run = spy_runner, spy_subprocess
    try:
        adapter = mod.Adapter(program)
        adapter.reset()
        for a in alphabet:
            try:
                adapter.run(a)
            except Exception:
                pass
        adapter.close()
    finally:
        runner.run, subprocess.run = real_run, real_subprocess
    return seen


def main() -> None:
    ws = Path(sys.argv[1]).resolve()
    grade = {"workspace": str(ws)}
    build = subprocess.run(["lake", "build"], cwd=ws / "lean", capture_output=True, text=True)
    grade["builds"] = build.returncode == 0
    if not grade["builds"]:
        grade["build_error"] = (build.stdout + build.stderr)[-2000:]
        print(json.dumps(grade, indent=1))
        (ws / "grade.json").write_text(json.dumps(grade, indent=1))
        return
    mod = load_adapter(ws)
    model = LeanModel(ws / "lean" / ".lake" / "build" / "bin" / "app-model")
    areas = model.areas()
    alphabet = model.alphabet("validation")
    grade["areas"] = areas
    grade["validation_alphabet_size"] = len(alphabet)
    grade["size"] = {"model_lines": code_lines(sorted((ws / "lean" / "App").rglob("*.lean"))),
                     "adapter_lines": code_lines([ws / "adapter.py"])}

    traces = transition_traces(model, areas, 4) + random_traces(alphabet, 400, 5, 20, 2026)
    grade["traces"] = len(traces)

    def run_against(program: Path) -> dict:
        results = validate(lambda: mod.Adapter(program), model, traces, workers=default_workers())
        bad = [r for r in results if r.first_mismatch]
        kinds = Counter(json.dumps(r.first_mismatch.get("action"))[:70] for r in bad)
        return {"mismatching_traces": len(bad), "steps": sum(len(r.trace) for r in results),
                "top_mismatches": kinds.most_common(5)}

    grade["reference"] = run_against(REFERENCE)
    print("reference:", grade["reference"]["mismatching_traces"], "of", len(traces), "traces mismatch")
    issued = commands_issued(mod, alphabet, REFERENCE)
    grade["commands_covered"] = sorted(f"{a} {b}".strip() for a, b in issued & COMMANDS)
    grade["commands_missing"] = sorted(f"{a} {b}".strip() for a, b in COMMANDS - issued)
    print(f"commands covered: {len(issued & COMMANDS)} of {len(COMMANDS)}")
    grade["mutants"] = {}
    for d in sorted(MUTANTS.iterdir()):
        r = run_against(d / "forge")
        grade["mutants"][d.name] = r["mismatching_traces"]
        print(f"  {d.name}: {r['mismatching_traces']} mismatching traces")
    (ws / "grade.json").write_text(json.dumps(grade, indent=1))
    print(json.dumps({k: v for k, v in grade.items() if k not in ("reference",)}, indent=1)[:3000])


if __name__ == "__main__":
    main()
