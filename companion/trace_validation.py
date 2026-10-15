"""Trace validation: run the same action sequences on the real application (through an
adapter) and on the Lean model, and compare outputs and observations step by step.

Each trace also yields the real application's transitions, which are passed to the model's
property evaluator, so a property can fail on the real code even where model and code agree.

`validate` does this for many traces, with three ways of doing less work:
- observe="changes": read the real state back only where the model says the state changed,
  and at the end of each trace (outputs are still compared at every step). Properties are
  then evaluated with the model's state where the real one was not read, so a violation
  found this way must be confirmed by `validate_trace`, which observes every step.
- Shared prefixes: traces are arranged in a tree, each prefix is run once, and branches
  continue from a snapshot (for adapters with `snapshot`/`restore`/`release`).
- cache: a `ValidationCache` keeps each trace's real results with the lines of the program
  it executed (recorded by companion.runner); a trace whose code did not change is not run
  again, only re-compared with the current model.
"""

import json
import multiprocessing
import os
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field

from companion import runner
from companion.abstraction import expressible
from companion.lean import LeanModel


@dataclass
class TraceResult:
    trace: list
    first_mismatch: dict | None = None
    violations: list = field(default_factory=list)   # (step, [property names]) on real code
    reused: bool = False                             # real results came from the cache


@dataclass
class Record:
    """What the real program did on one trace."""
    outs: list                    # adapter output after each step
    obs: list                     # observation after each step, or None where not read
    initial: dict | None          # observation before the first step, if read
    footprint: dict | None        # file -> lines of the program executed, if recorded
    mode: str = "all"             # which observation plan produced it


@dataclass
class Stats:
    traces: int = 0
    reused: int = 0               # traces whose real results came from the cache
    steps_run: int = 0            # actions run on the program (a shared prefix counts once)
    program_calls: int = 0        # runs of the program, including observations
    seconds: float = 0.0
    coverage: dict | None = None  # file -> lines executed by any trace; None if some run was not recorded


def action_kind(action) -> str:
    """The constructor name of an action. Actions of the form {"cmd": ..., "ctx": ...} are
    classified by their command."""
    if isinstance(action, dict) and "cmd" in action:
        return action_kind(action["cmd"])
    return action if isinstance(action, str) else next(iter(action))


def transition_traces(model: LeanModel, areas: list, depth: int) -> list:
    """Traces that cover every transition of the search areas: a shortest path to each
    state reachable within `depth`, extended by every action of that area's alphabet.
    Random traces miss behavior that needs setup first; these do not."""
    traces = []
    for area in areas:
        alphabet = model.alphabet(area)
        for path in [[]] + model.paths(area, depth, 10**6):
            traces += [path + [a] for a in alphabet]
    return traces


def default_workers() -> int:
    """A modest number of worker processes. More finish sooner but cost much more CPU in
    total: they compete for cores and split shared prefixes more finely (measured on 24
    cores: 4 workers took 21 s and 95 CPU-seconds for a cold validation, 22 took 12 s and 238)."""
    return max(1, min(6, (os.cpu_count() or 4) // 4))


def random_traces(alphabet: list, n: int, min_len: int, max_len: int, seed: int, kind=action_kind) -> list:
    """Random traces that pick an action kind uniformly first, so that the many `create`
    variants do not crowd out everything else."""
    rng = random.Random(seed)
    by_kind = defaultdict(list)
    for a in alphabet:
        by_kind[kind(a)].append(a)
    kinds = sorted(by_kind)
    return [[rng.choice(by_kind[rng.choice(kinds)]) for _ in range(rng.randint(min_len, max_len))]
            for _ in range(n)]


def validate_trace(adapter, model: LeanModel, trace: list) -> TraceResult:
    adapter.reset()
    expected = model.replay(trace)
    result = TraceResult(trace)
    before = adapter.observe()
    if before != expected["initial"]:
        result.first_mismatch = {"step": -1, "expected_obs": expected["initial"], "real_obs": before}
    transitions = []
    for i, (action, exp) in enumerate(zip(trace, expected["steps"])):
        out = adapter.run(action)
        after = adapter.observe()
        transitions.append((i, {"before": before, "action": action, "out": out, "after": after}))
        if result.first_mismatch is None and (out != exp["out"] or after != exp["obs"]):
            result.first_mismatch = {"step": i, "action": action,
                                     "expected_out": exp["out"], "real_out": out,
                                     "expected_obs": exp["obs"], "real_obs": after}
        before = after
    # Properties are evaluated on the real transitions, except those the model's types
    # cannot express (they are mismatches anyway).
    parseable = [(i, t) for i, t in transitions if expressible(t)]
    for (i, _), names in zip(parseable, evaluate(model, [t for _, t in parseable])):
        if names:
            result.violations.append((i, names))
    return result


def evaluate(model: LeanModel, transitions: list) -> list:
    """The violated property names of each transition. A batch the model cannot parse is
    evaluated one transition at a time, and transitions that still fail count as violating
    nothing (they come from an adapter whose output does not fit the model's types)."""
    out = []
    for k in range(0, len(transitions), 2000):
        batch = transitions[k:k + 2000]
        try:
            out += model.eval(batch)
        except Exception:
            for t in batch:
                try:
                    out += model.eval([t])
                except Exception:
                    out.append([])
    return out


def validate(make_adapter, model: LeanModel, traces: list, workers: int = 8, observe: str = "all",
             cache=None, stats: Stats | None = None) -> list[TraceResult]:
    """Validate many traces. Execution happens in forked worker processes (not threads:
    starting the program from many threads of one process is serialized), each with its own
    adapter and data dir; `make_adapter` need not be picklable."""
    start = time.time()
    # Start the workers before anything large is in memory: every program run forks a
    # worker, and forking a process with a large heap costs more CPU.
    ctx = multiprocessing.get_context("fork")
    pool = (ctx.Pool(workers, initializer=_init_worker, initargs=(make_adapter,))
            if workers > 1 and (cache is None or not cache.covers(traces, observe)) else None)
    expected = []
    for i in range(0, len(traces), 2000):
        expected += model.replay_many(traces[i:i + 2000])
    plans = [_plan(e, observe) for e in expected]
    records: list = [None] * len(traces)
    keys = [json.dumps(t, sort_keys=True) for t in traces]
    if cache is not None:
        for i, k in enumerate(keys):
            records[i] = cache.get(k, observe)
    reused = sum(r is not None for r in records)

    todo = [i for i, r in enumerate(records) if r is None]
    tasks = [(g, [traces[i] for i in g], [plans[i] for i in g], observe) for g in _groups(traces, todo, workers)]
    if pool is not None:
        with pool:
            parts = pool.map(_execute_group, tasks, chunksize=1)
    else:
        _init_worker(make_adapter)
        parts = [_execute_group(t) for t in tasks]
    if cache is not None and not todo:
        parts = []
    calls = steps = 0
    for recs, c, n in parts:
        calls, steps = calls + c, steps + n
        for i, rec in recs.items():
            records[i] = rec
    if cache is not None:
        for k, rec in zip(keys, records):
            cache.put(k, rec)

    coverage: dict | None = {}
    for rec in records:
        if rec.footprint is None:
            coverage = None
            break
        for f, lines in rec.footprint.items():
            coverage.setdefault(f, set()).update(lines)

    results = _compare(model, traces, expected, records)
    ran = set(todo)
    for i, r in enumerate(results):
        r.reused = i not in ran
    if stats is not None:
        stats.traces, stats.reused = len(traces), reused
        stats.steps_run, stats.program_calls = steps, calls
        stats.coverage = coverage
        stats.seconds = round(time.time() - start, 1)
    return results


_make_adapter = None


def _init_worker(make_adapter) -> None:
    global _make_adapter
    _make_adapter = make_adapter


def _plan(expected: dict, observe: str) -> tuple:
    """Where to read the real state: (before the first step?, after each step?)."""
    steps = expected["steps"]
    if observe == "all":
        return True, [True] * len(steps)
    prev, after = expected["initial"], []
    for j, s in enumerate(steps):
        after.append(s["obs"] != prev or j == len(steps) - 1)
        prev = s["obs"]
    return False, after


def _groups(traces: list, todo: list, workers: int) -> list:
    """Split traces into groups that share a prefix (each group runs as one prefix tree).
    Groups start by first action; any group larger than its fair share is split by the
    next action, so that no worker is left with most of the work. A split costs only the
    re-run of a short prefix."""
    target = max(8, len(todo) // (workers * 4))
    done, pending = [], [(1, todo)]
    while pending:
        k, idx = pending.pop()
        if len(idx) <= target or k > 6:
            done.append(idx)
            continue
        by = defaultdict(list)
        for i in idx:
            by[json.dumps(traces[i][:k], sort_keys=True)].append(i)
        if len(by) == 1 and all(len(traces[i]) < k for i in idx):
            done.append(idx)
            continue
        pending += [(k + 1, g) for g in by.values()]
    return sorted(done, key=len, reverse=True)      # largest first, for better packing


def _execute_group(task):
    """Run one group of traces (given with their observation plans); returns their records
    keyed by the traces' original indices."""
    indices, traces, plans, observe = task
    local = list(range(len(indices)))
    adapter = _make_adapter()
    calls = runner.calls
    try:
        if all(hasattr(adapter, m) for m in ("snapshot", "restore", "release")):
            recs, steps = _execute_shared(adapter, traces, plans, local, observe)
        else:
            recs = {i: _execute_one(adapter, traces[i], plans[i], observe) for i in local}
            steps = sum(len(t) for t in traces)
    finally:
        adapter.close()
    return {indices[i]: r for i, r in recs.items()}, runner.calls - calls, steps


def _recorded(adapter_call, fp: dict):
    """Run one adapter call, adding the program lines it executes to `fp`. Also returns
    whether the footprint is complete: every run of the program was recorded (and there
    was at least one)."""
    before = runner.calls
    with runner.recording() as rec:
        value = adapter_call()
    for f, lines in rec.lines.items():
        fp.setdefault(f, set()).update(lines)
    return value, rec.complete and runner.calls > before


def _freeze(fp: dict, traced: bool) -> dict | None:
    return {f: sorted(lines) for f, lines in fp.items()} if traced else None


def _execute_one(adapter, trace: list, plan: tuple, observe: str) -> Record:
    adapter.reset()
    fp, traced = {}, True
    initial = None
    if plan[0]:
        initial, t = _recorded(adapter.observe, fp)
        traced &= t
    outs, obs = [], []
    for action, read in zip(trace, plan[1]):
        out, t = _recorded(lambda: adapter.run(action), fp)
        traced &= t
        outs.append(out)
        if read:
            o, t = _recorded(adapter.observe, fp)
            traced &= t
            obs.append(o)
        else:
            obs.append(None)
    return Record(outs, obs, initial, _freeze(fp, traced), observe)


class _Node:
    __slots__ = ("action", "children", "ends", "read", "out", "obs", "fp", "traced")

    def __init__(self, action=None):
        self.action, self.children, self.ends, self.read = action, {}, [], False
        self.out = self.obs = None
        self.fp, self.traced = {}, True


def _execute_shared(adapter, traces: list, plans: list, indices: list, observe: str):
    """Run the traces as a prefix tree: each node once, branching from snapshots."""
    root, read_initial = _Node(), any(plans[i][0] for i in indices)
    for i in indices:
        node = root
        for action, read in zip(traces[i], plans[i][1]):
            key = json.dumps(action, sort_keys=True)
            node = node.children.setdefault(key, _Node(action))
            node.read |= read
        node.ends.append(i)
    adapter.reset()
    initial = None
    if read_initial:
        initial, root.traced = _recorded(adapter.observe, root.fp)
    steps = 0

    # Depth-first: a node's children run one after another, each from the state after the
    # node (restored from a snapshot when there is more than one child).
    def walk(node):
        nonlocal steps
        kids = list(node.children.values())
        snap = adapter.snapshot() if len(kids) > 1 else None
        for n, child in enumerate(kids):
            if n > 0:
                adapter.restore(snap)
            child.out, t = _recorded(lambda: adapter.run(child.action), child.fp)
            child.traced &= t
            if child.read:
                child.obs, t = _recorded(adapter.observe, child.fp)
                child.traced &= t
            steps += 1
            walk(child)
        if snap is not None:
            adapter.release(snap)
    walk(root)

    recs = {}
    def collect(node, path):
        for i in node.ends:
            fp, traced = dict(), True
            for f, lines in root.fp.items():
                fp.setdefault(f, set()).update(lines)
            traced &= root.traced
            for n in path:
                for f, lines in n.fp.items():
                    fp.setdefault(f, set()).update(lines)
                traced &= n.traced
            recs[i] = Record([n.out for n in path], [n.obs for n in path],
                             initial if plans[i][0] else None, _freeze(fp, traced), observe)
        for child in node.children.values():
            collect(child, path + [child])
    collect(root, [])
    return recs, steps


def _compare(model: LeanModel, traces: list, expected: list, records: list) -> list[TraceResult]:
    results, transitions, owners = [], [], []
    for i, (trace, exp, rec) in enumerate(zip(traces, expected, records)):
        result = TraceResult(trace)
        if rec.initial is not None and rec.initial != exp["initial"]:
            result.first_mismatch = {"step": -1, "expected_obs": exp["initial"], "real_obs": rec.initial}
        before = rec.initial if rec.initial is not None else exp["initial"]
        for j, (action, e) in enumerate(zip(trace, exp["steps"])):
            out, real = rec.outs[j], rec.obs[j]
            after = real if real is not None else e["obs"]
            if result.first_mismatch is None and (out != e["out"] or (real is not None and real != e["obs"])):
                result.first_mismatch = {"step": j, "action": action, "expected_out": e["out"], "real_out": out,
                                         "expected_obs": e["obs"], "real_obs": real}
            t = {"before": before, "action": action, "out": out, "after": after}
            if expressible(t):
                transitions.append(t)
                owners.append((i, j))
            before = after
        results.append(result)
    for (i, j), names in zip(owners, evaluate(model, transitions)):
        if names:
            results[i].violations.append((j, names))
    return results
