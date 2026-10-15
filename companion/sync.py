"""Keeping the model in step with the code: synthesis, trace validation and repair.

The model is written by an LLM agent for each application (`synthesize`), and it must keep
doing what the code does as the coding agent changes the code. After every turn:

1. Build the model with the ledger's properties, and run trace validation (traces chosen by
   transition coverage, plus random ones) on the real program and on the model.
2. Find the changed code that no trace reached (companion/coverage.py): code changed since
   the model was last brought up to date, which validation therefore says nothing about,
   such as a command the agent just added.
3. If the model no longer matches the code, or there is such changed code, the LLM agent
   repairs the model and adapter, with the mismatches, the code's diff and the unreached
   code in front of it, until validation passes, for a few rounds at most. It is told to
   follow the code even where the code looks wrong: the model describes the code, and the
   ledger's properties are what hold the developer's intent.
4. Constraints whose predicates no longer typecheck after the model changed get one repair
   round from the LLM (and constraints that could not be formalized before are retried:
   the model may now have the vocabulary they need).
5. Once the model matches the code, the code as it is now becomes the baseline for step 2.

The agent works in the application's workspace (companion/workspace.py) and runs
validation itself (`validate.py`, which calls `validate_main`).
"""

import difflib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from companion import workspace
from companion.config import Config
from companion.coverage import uncovered_changes
from companion.lean import LeanModel
from companion.ledger import Ledger, typecheck
from companion.trace_validation import Stats, default_workers, random_traces, transition_traces, validate
from companion.validation_cache import ValidationCache

PROMPTS = Path(__file__).parent / "prompts"
START = "Read TASK.md in the current directory and complete the task it describes."


class ModelBuildError(RuntimeError):
    """The model (or the executable with the ledger's properties) does not build."""


@dataclass
class Validation:
    results: list = field(default_factory=list)      # TraceResult per trace
    stats: Stats = field(default_factory=Stats)
    error: str | None = None                         # the model does not build, or the adapter fails
    model: LeanModel | None = None

    @property
    def mismatched(self) -> list:
        return [r for r in self.results if r.first_mismatch]

    @property
    def matches(self) -> bool:
        return self.error is None and not self.mismatched


# Building -----------------------------------------------------------------------------------

def _lake(cfg: Config, *targets: str) -> subprocess.CompletedProcess:
    return subprocess.run(["lake", "build", *targets], cwd=cfg.lean_dir, capture_output=True, text=True)


def build_model_modules(cfg: Config) -> None:
    """Build the model's modules (those the ledger module imports)."""
    if cfg.workspace:
        workspace.refresh(cfg)
    result = _lake(cfg, *(f"+{m}" for m in cfg.model_imports()))
    if result.returncode != 0:
        raise ModelBuildError("the model does not build:\n" + _tail(result))


def build(cfg: Config, ledger: Ledger, llm=None, retry: bool = False) -> list:
    """Typecheck the ledger and build the executable with its properties. Returns the ids
    that failed to typecheck. With `retry` (after the model changed), entries that could not
    be formalized before are tried again, and with an `llm`, entries that fail get one repair
    round. Raises ModelBuildError if the model itself does not build."""
    build_model_modules(cfg)
    if retry:
        for e in ledger.entries:
            if e.status == "unformalized":
                e.status, e.error = "proposed", None
    failed = typecheck(ledger, cfg)
    if failed and retry and llm is not None:
        from companion.extract import repair_predicates
        repair_predicates(cfg, ledger, failed, llm)
        failed = typecheck(ledger, cfg)
    result = _lake(cfg, cfg.exe)
    if result.returncode != 0:
        raise ModelBuildError(f"could not build {cfg.exe}:\n" + _tail(result))
    return failed


def _tail(result, n: int = 3000) -> str:
    return (result.stdout + result.stderr)[-n:]


# Validation ---------------------------------------------------------------------------------

def validation_traces(cfg: Config, model: LeanModel, quick: bool = False) -> list:
    """Traces covering every transition of the search areas, random traces, and the traces
    on which the model was once found to be wrong (`add_validation_traces`)."""
    c = cfg.check
    traces = transition_traces(model, model.areas(), 2 if quick else c.get("transition_depth", 4))
    traces += random_traces(model.alphabet("validation"), 40 if quick else c.get("random_traces", 200),
                            5, 20, c.get("random_seed", 0))
    return traces + extra_traces(cfg)


def extra_traces(cfg: Config) -> list:
    path = cfg.state_dir / "extra-traces.json"
    return json.loads(path.read_text()) if path.exists() else []


def add_validation_traces(cfg: Config, traces: list) -> None:
    """Keep traces on which the model and the code disagreed (a counterexample of the model
    that the code does not reproduce): validation runs them from now on."""
    known = extra_traces(cfg)
    known += [t for t in traces if t not in known]
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "extra-traces.json").write_text(json.dumps(known))


def run_validation(cfg: Config, model: LeanModel, quick: bool = False) -> Validation:
    os.environ.setdefault("COMPANION_TMP", str(cfg.state_dir / "tmp"))   # keep work files off a RAM /tmp
    cache = ValidationCache(cfg.state_dir / "validation-cache.json", cfg.source_files(), cfg.adapter_file())
    stats = Stats()
    try:
        traces = validation_traces(cfg, model, quick)
        results = validate(cfg.make_adapter, model, traces, cfg.check.get("workers") or default_workers(),
                           observe=cfg.check.get("observe", "changes"), cache=cache, stats=stats)
    except Exception as e:          # an adapter that crashes, or a model executable that fails
        return Validation(stats=stats, error=f"validation failed: {type(e).__name__}: {e}"[:3000], model=model)
    cache.save()
    return Validation(results, stats, model=model)


def build_and_validate(cfg: Config, ledger: Ledger, log=print, llm=None, retry: bool = False) -> Validation:
    try:
        failed = build(cfg, ledger, llm, retry)
    except ModelBuildError as e:
        ledger.save()
        log(str(e).splitlines()[0])
        return Validation(error=str(e))
    ledger.save()
    if failed:
        log(f"not formalized (Lean errors): {', '.join(failed)}")
    v = run_validation(cfg, LeanModel(cfg.exe_path))
    s = v.stats
    log(f"trace validation: {s.traces} traces, {s.reused} reused from the cache, {s.steps_run} steps run, "
        f"{len(v.mismatched)} mismatching, {s.seconds}s" + (f"; {v.error}" if v.error else ""))
    return v


# Baseline and unreached changes ---------------------------------------------------------------

def current_sources(cfg: Config) -> dict:
    return {str(p): p.read_text() for p in cfg.source_files() if p.exists()}


def baseline(cfg: Config) -> dict | None:
    """The program's sources when the model was last brought up to date."""
    path = cfg.state_dir / "synced.json"
    return json.loads(path.read_text()) if path.exists() else None


def save_baseline(cfg: Config) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "synced.json").write_text(json.dumps(current_sources(cfg)))


def gaps(cfg: Config, v: Validation) -> list | None:
    """Changed code since the baseline that no validation trace ran; None if unknown."""
    if v.error:
        return None
    base = baseline(cfg)
    if base is None:
        return []
    return uncovered_changes(base, current_sources(cfg), v.stats.coverage)


# The per-turn step ----------------------------------------------------------------------------

@dataclass
class SyncResult:
    validation: Validation
    gaps: list | None                    # changed code no trace reaches (after any repair)
    repair: dict | None = None           # what the agent did, if it was asked


def sync(cfg: Config, agent=None, llm=None, log=print) -> SyncResult:
    """Build, validate, and repair the model if it has fallen behind the code (only for a
    workspace, and only with an agent)."""
    ledger = Ledger.load(cfg.ledger_path)
    v = build_and_validate(cfg, ledger, log)
    g = gaps(cfg, v)
    info = None
    if g:
        log(f"changed code that no trace reaches: {len(g)} statements")
    if agent is not None and cfg.workspace and (not v.matches or g):
        v, g, info = repair(cfg, agent, llm, v, g, log)
    if v.matches and (not g or info):
        save_baseline(cfg)
    return SyncResult(v, g, info)


def repair(cfg: Config, agent, llm, v: Validation, g: list | None, log=print, unreached: list | None = None) -> tuple:
    """Ask the agent to bring the model up to date, for at most `repair_rounds` rounds.
    Returns the new validation, the unreached changes after it, and a record of the rounds.
    `unreached` lists ledger entries whose situation the search never reaches: the agent
    widens the search areas so that it does. If the agent leaves a model that does not
    build, the previous model is put back."""
    rounds = cfg.agent.get("repair_rounds", 2)
    saved = workspace.save_model(cfg)
    attempts, asked = [], g
    for n in range(rounds):
        prompt = repair_prompt(cfg, v, asked if n == 0 else None, n, unreached if n == 0 else None)
        log(f"repairing the model (round {n + 1} of {rounds}): "
            + ("it does not build" if v.error else f"{len(v.mismatched)} mismatching traces")
            + (f", {len(asked)} unreached changed statements" if n == 0 and asked else "")
            + (f", {len(unreached)} rules whose situation the search never reaches" if n == 0 and unreached else ""))
        attempts.append(_run_agent(cfg, agent, prompt, f"repair-{n + 1}"))
        v = build_and_validate(cfg, Ledger.load(cfg.ledger_path), log, llm, retry=True)
        if v.matches:
            break
    if v.error and saved:
        log("the repaired model does not work; putting the previous one back")
        workspace.restore_model(cfg, saved)
        v = build_and_validate(cfg, Ledger.load(cfg.ledger_path), log, llm, retry=True)
    g = gaps(cfg, v)
    return v, g, {"rounds": len(attempts), "fixed": v.matches, "asked_about_unreached": bool(asked),
                  "asked_about_situations": [e.id for e in unreached or []],
                  "cost_usd": _total(attempts, "cost_usd"), "seconds": _total(attempts, "seconds"),
                  "attempts": attempts}


def synthesize(cfg: Config, agent, llm=None, log=print) -> dict:
    """Have the agent write the model and adapter from the program's code, then repair them
    until they match (a few rounds at most)."""
    workspace.refresh(cfg)
    attempts = [_run_agent(cfg, agent, synthesis_prompt(cfg), "synthesize")]
    v = build_and_validate(cfg, Ledger.load(cfg.ledger_path), log, llm, retry=True)
    info = None
    if not v.matches:
        v, _, info = repair(cfg, agent, llm, v, None, log)
    if v.matches:
        save_baseline(cfg)
    summary = {"matches": v.matches, "error": v.error, "traces": len(v.results), "mismatches": len(v.mismatched),
               "synthesis": attempts[0], "repair": info,
               "cost_usd": _total(attempts + (info["attempts"] if info else []), "cost_usd"),
               "seconds": _total(attempts + (info["attempts"] if info else []), "seconds")}
    (cfg.state_dir / "synthesis.json").write_text(json.dumps(summary, indent=1))
    return summary


def _run_agent(cfg: Config, agent, task: str, kind: str) -> dict:
    (cfg.workspace / "TASK.md").write_text(task)
    log_path = cfg.state_dir / "agent" / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.jsonl"
    result = agent.run(START, cwd=cfg.workspace, add_dirs=[cfg.program.parent], log=log_path)
    return {"kind": kind, "log": str(log_path), **result}


def _total(attempts: list, key: str):
    values = [a.get(key) for a in attempts if a.get(key) is not None]
    return round(sum(values), 4) if values else None


# Prompts ------------------------------------------------------------------------------------

def _fill(template: str, cfg: Config, **extra) -> str:
    text = (PROMPTS / template).read_text()
    values = {"PROGRAM": str(cfg.program), "PROGRAM_DIR": str(cfg.program.parent), "NS": cfg.namespace,
              "PYTHON": workspace.python(), **extra}
    for k, val in values.items():
        text = text.replace(f"<<{k}>>", val)
    return text


def synthesis_prompt(cfg: Config) -> str:
    return _fill("synthesize.md", cfg)


def repair_prompt(cfg: Config, v: Validation, g: list | None, attempt: int = 0, unreached: list | None = None) -> str:
    sections = []
    if attempt:
        sections.append(f"## Note\n\nThis is attempt {attempt + 1}: an earlier attempt left the problems below.")
    diff = source_diff(cfg)
    if diff:
        sections.append("## What changed in the program since the model last matched it\n\n```diff\n" + diff + "\n```")
    if v.error:
        sections.append("## The model or adapter does not work\n\n```\n" + v.error[-3000:] + "\n```")
    elif v.mismatched:
        sections.append("## Where the model and the program disagree\n\n" + describe_mismatches(v))
    if g:
        lines = "\n".join(f"- `{x['file']}:{x['line']}`: `{x['text']}`" for x in g[:60])
        more = f"\n- ... and {len(g) - 60} more" if len(g) > 60 else ""
        sections.append("## Changed code that no validation trace reaches\n\n"
                        "These statements are new or changed since the model last matched the program, and no "
                        "validation trace executes them, so validation cannot tell whether the model covers them. "
                        "If the program's commands can reach them, extend the model and adapter (and the areas and "
                        "`validationAlphabet`) so that traces do. If they cannot be reached through the program's "
                        "interface, or do not affect its state or outputs, leave them and say so.\n\n" + lines + more)
    if unreached:
        depth = cfg.check.get("search_depth", 8)
        rules = "\n".join(f"- {e.id}: {e.text}\n  Situation: {e.situation}\n  ```lean\n  {e.situation_predicate.strip()}\n  ```"
                          for e in unreached)
        sections.append("## Rules whose situation the search never reaches\n\n"
                        f"The companion searches each area of the model exhaustively, up to {depth} steps from the empty "
                        "state, within the area's bound. For each rule below, no area ever reaches the situation the "
                        "rule is about, so the search says nothing about it. Change the areas so that it does: add "
                        "the actions needed to bring the situation about to an area (or add an area), and loosen the "
                        "bound if the situation needs more resources. Keep areas small, since their size multiplies. "
                        "Do not change the rules. If a situation cannot happen in the program at all, say so.\n\n"
                        + rules)
    active = Ledger.load(cfg.ledger_path).active()
    if active:
        rules = "\n".join(f"- {e.id}: {e.text}\n  ```lean\n  {e.predicate.strip()}\n  ```" for e in active)
        sections.append("## The developer's rules (they use these names of the model)\n\n" + rules)
    return _fill("repair.md", cfg, SECTIONS="\n\n".join(sections))


def source_diff(cfg: Config, limit: int = 600) -> str:
    base = baseline(cfg)
    if base is None:
        return ""
    out = []
    for path, new in sorted(current_sources(cfg).items()):
        old = base.get(path, "")
        if old != new:
            name = Path(path).name
            out += difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", lineterm="")
    if len(out) > limit:
        out = out[:limit] + [f"... ({len(out) - limit} more lines of diff)"]
    return "\n".join(out)


def describe_mismatches(v: Validation, limit: int = 5) -> str:
    bad = v.mismatched
    by_action: dict = {}
    for r in bad:
        a = r.first_mismatch.get("action")
        by_action.setdefault(_short(a, 200) if a is not None else "the initial observation", []).append(r)
    # One example per kind of action first, shortest trace first.
    examples = [min(rs, key=lambda r: r.first_mismatch["step"]) for rs in by_action.values()][:limit]
    parts = [f"{len(bad)} of {len(v.results)} validation traces disagree. The first difference of some of them:"]
    for r in examples:
        m = r.first_mismatch
        step = m["step"]
        parts.append("\n```\ntrace: " + _short(r.trace[:step + 1] if step >= 0 else [], 1200))
        for k in ("expected_out", "real_out", "expected_obs", "real_obs"):
            if k in m and m[k] is not None:
                parts.append(f"{k}: {_short(m[k], 1200)}")
        parts.append("```")
    counts = sorted(((len(rs), k) for k, rs in by_action.items()), reverse=True)[:10]
    parts.append("\nDisagreeing traces by the action where they first differ:\n"
                 + "\n".join(f"- {n}: {k}" for n, k in counts))
    return "\n".join(parts)


def _short(value, n: int) -> str:
    s = value if isinstance(value, str) else json.dumps(value)
    return s if len(s) <= n else s[:n] + " ..."


# The agent's validation command ---------------------------------------------------------------

def validate_main(ws: Path, quick: bool = False) -> int:
    """What `validate.py` in a workspace runs: build the model alone, validate it, and print
    what disagrees. Exit code 0 when everything matches."""
    cfg = Config.load(ws)
    workspace.refresh(cfg)
    result = _lake(cfg, cfg.model_exe)
    if result.returncode != 0:
        print("lake build failed:\n" + _tail(result, 4000))
        return 1
    v = run_validation(cfg, LeanModel(cfg.lean_dir / ".lake" / "build" / "bin" / cfg.model_exe), quick)
    if v.error:
        print(v.error)
        return 1
    steps = sum(len(r.trace) for r in v.results)
    print(f"{len(v.results)} traces, {steps} steps, {len(v.mismatched)} mismatching")
    if v.mismatched:
        print("\n" + describe_mismatches(v, 4))
    g = gaps(cfg, v)
    if g:
        print(f"\nchanged code that no trace reaches ({len(g)} statements):")
        for x in g[:40]:
            print(f"  {x['file']}:{x['line']}: {x['text']}")
    return 0 if v.matches else 2
