"""Shared setup for the tests: paths, the forge program and its edits, quick configs.

The tests that use forge need the SlopCodeBench problems (studies/feasibility/fetch_data.sh)
and the Lean build (`cd lean && lake build`); they are skipped otherwise.
"""

import json
import os
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Adapters' data dirs go to disk, not /tmp (which may be in RAM), and tests use few workers:
# validation forks a process per program run, and many workers at once can overload a machine.
os.environ.setdefault("COMPANION_TMP", str(ROOT / "data" / "tmp"))
WORKERS = 2
LEAN = ROOT / "lean"
FORGE_MODEL = LEAN / ".lake" / "build" / "bin" / "forge-model"
REFERENCE_DIR = ROOT / "data" / "scb-problems" / "forge" / "solutions" / "checkpoint_8"
REFERENCE = REFERENCE_DIR / "forge"
EXAMPLE_LEDGER = ROOT / "apps" / "forge" / "example-ledger.json"

requires_forge = unittest.skipUnless(REFERENCE.exists() and FORGE_MODEL.exists(),
                                     "needs data/scb-problems (fetch_data.sh) and `lake build`")


def trace_set(model, seed: int) -> list:
    """Traces deep enough to reach every seeded bug (some need four steps of setup): every
    transition of the allocation and module areas to depth 3, of the access area to depth 2,
    and some random traces."""
    from companion.trace_validation import random_traces, transition_traces
    return (transition_traces(model, ["alloc", "module"], 3) + transition_traces(model, ["access"], 2)
            + random_traces(model.alphabet("validation"), 40, 3, 12, seed))


def one_trace_at_a_time(model, program: Path, traces: list) -> list:
    """The reference results: each trace run on its own from the initial state, comparing
    outputs and the whole state after every step (`validate_trace`), in plain worker
    processes so that it does not take minutes."""
    import multiprocessing
    chunks = [(model, program, traces[i::WORKERS]) for i in range(WORKERS)]
    with multiprocessing.get_context("fork").Pool(WORKERS) as pool:
        parts = pool.map(_one_chunk, chunks)
    results = [None] * len(traces)
    for i in range(WORKERS):
        results[i::WORKERS] = parts[i]
    return results


def _one_chunk(job):
    from companion.adapters.forge import ForgeAdapter
    from companion.trace_validation import validate_trace
    model, program, traces = job
    adapter = ForgeAdapter(program)
    try:
        return [validate_trace(adapter, model, t) for t in traces]
    finally:
        adapter.close()


def edits(name: str) -> list:
    """The text edits of a seeded bug (studies/feasibility/make_mutants.py)."""
    from studies.feasibility.make_mutants import MUTANTS
    return MUTANTS[name]


def program_copy(directory: Path, changes: list = ()) -> Path:
    """A copy of forge's checkpoint 8 in `directory`, with text edits applied; returns the
    program path."""
    directory.mkdir(parents=True, exist_ok=True)
    for f in ("forge", "forge.py"):
        shutil.copy(REFERENCE_DIR / f, directory / f)
    apply(directory / "forge", changes)
    return directory / "forge"


def apply(program: Path, changes: list) -> None:
    """Apply text edits to a program copy's forge.py, as an agent's edit would."""
    source = program.with_suffix(".py")
    text = source.read_text()
    for old, new in changes:
        assert text.count(old) == 1, f"edit does not apply: {old[:60]!r}"
        text = text.replace(old, new)
    source.write_text(text)


def quick_config(directory: Path, program: Path = REFERENCE, ledger: Path | None = None, **check):
    """A config for forge with a small trace budget, its state in `directory`, and the ledger
    initialized from `ledger` (empty if None)."""
    from companion.config import Config
    from companion.ledger import Ledger
    settings = {"transition_depth": 2, "random_traces": 20, "random_seed": 1, "search_depth": 6, **check}
    base = json.loads((ROOT / "apps" / "forge" / "companion.json").read_text())
    base.update({"root": str(ROOT), "state_dir": str(directory / "state"), "check": settings})
    path = directory / "companion.json"
    path.write_text(json.dumps(base))
    cfg = Config.load(path, str(program))
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        shutil.copy(ledger, cfg.ledger_path)
    else:
        Ledger(cfg.ledger_path).save()
    return cfg


QUICK_CHECK = {"transition_depth": 2, "random_traces": 20, "random_seed": 1, "search_depth": 6, "workers": WORKERS}


def forge_workspace(directory: Path, program: Path, ledger: Path | None = None, seeded: bool = True, **check):
    """A companion workspace for a forge program, with a small trace budget. If `seeded`, it
    starts with the hand-built forge model and adapter, standing in for ones an LLM wrote;
    otherwise it has no model yet."""
    from companion import workspace
    cfg = workspace.create(directory, program, app="forge", check={**QUICK_CHECK, **check})
    if seeded:
        seed_forge(cfg)
    if ledger is not None:
        shutil.copy(ledger, cfg.ledger_path)
    return cfg


def seed_forge(cfg) -> None:
    """Put the hand-built forge model (lean/Forge) and adapter into a workspace."""
    from companion import workspace
    workspace.seed(cfg, {f: LEAN / "Forge" / f for f in ("Model.lean", "Props.lean", "App.lean")},
                   ["Forge.App", "Forge.Props"], ROOT / "companion" / "adapters" / "forge.py", "ForgeAdapter")


class StandInAgent:
    """Stands in for the LLM agent: each run applies the next of the given edits to the
    workspace (a function of the workspace directory), and records what it was asked."""

    def __init__(self, *edits):
        self.edits, self.calls = list(edits), []

    def run(self, prompt, cwd, add_dirs=(), log=None):
        self.calls.append({"prompt": prompt, "task": (Path(cwd) / "TASK.md").read_text(),
                           "cwd": Path(cwd), "add_dirs": [Path(d) for d in add_dirs]})
        if self.edits:
            self.edits.pop(0)(Path(cwd))
        return {"ok": True, "seconds": 1, "cost_usd": 0.01, "turns": 1, "result": "done"}


def edit_file(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, f"edit does not apply: {old[:60]!r}"
    path.write_text(text.replace(old, new))


# Two rules about tags, each with the situation it is about. The hand-built model's search
# areas reach the first situation (a module given a second tag with the same key), but not the
# second: no area contains the tag update command.
TAG_KEYS_UNIQUE = {
    "text": "A module has at most one tag per key.",
    "predicate": "fun t =>\n  let keys := t.after.tags.map fun g => (g.module, g.key)\n  keys.eraseDups.length == keys.length",
    "situation": "A tag is created with a key its module already has.",
    "situation_predicate": "fun t =>\n  match t.action.cmd with\n  | .tagCreate m k _ => t.before.tags.any (fun g => g.module == m && g.key == k)\n  | _ => false",
}
TAG_UPDATE_REJECTED = {
    "text": "Updating a tag is always rejected.",
    "predicate": "fun t => t.action.cmd != .tagUpdate || t.out.body.isError",
    "situation": "A tag update is requested.",
    "situation_predicate": "fun t => t.action.cmd == .tagUpdate",
}


def ledger_file(directory: Path, *rules: dict) -> Path:
    """A ledger holding the given rules (as c1, c2, ...), stated in turn 1."""
    entries = [{"id": f"c{n}", "kind": "state", "status": "proposed", "confidence": "high",
                "provenance": [{"turn": 1, "quote": r["text"]}], "history": [], "error": None, **r}
               for n, r in enumerate(rules, 1)]
    path = directory / "ledger.json"
    path.write_text(json.dumps({"turn": 1, "entries": entries}))
    return path
