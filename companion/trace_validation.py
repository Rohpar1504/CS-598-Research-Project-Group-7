"""Trace validation: run the same action sequences on the real application (through an
adapter) and on the Lean model, and compare outputs and observations step by step.

Each trace also yields the real application's transitions, which are passed to the model's
property evaluator, so a property can fail on the real code even where model and code agree.
"""

import json
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from companion.lean import LeanModel


@dataclass
class TraceResult:
    trace: list
    first_mismatch: dict | None = None
    violations: list = field(default_factory=list)   # (step, [property names]) on real code


def action_kind(action) -> str:
    return action if isinstance(action, str) else next(iter(action))


def abstracts_cleanly(value) -> bool:
    """False if the adapter could not express some part of a real output or observation in
    the model's terms (the model's types would reject it)."""
    text = json.dumps(value)
    return not any(marker in text for marker in ('"unexpected', '"exitCodeMismatch"', '"unknown:', '"unobservable"'))


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
        if "unobservable" not in before and "unobservable" not in after:
            transitions.append((i, {"before": before, "action": action, "out": out, "after": after}))
        if result.first_mismatch is None and (out != exp["out"] or after != exp["obs"]):
            result.first_mismatch = {"step": i, "action": action,
                                     "expected_out": exp["out"], "real_out": out,
                                     "expected_obs": exp["obs"], "real_obs": after}
        before = after
    # Properties are evaluated on the real transitions. Outputs that did not abstract
    # cleanly cannot be parsed by the model, so they are left out.
    parseable = [(i, t) for i, t in transitions if abstracts_cleanly(t)]
    if parseable:
        try:
            names = model.eval([t for _, t in parseable])
            result.violations = [(i, v) for (i, _), v in zip(parseable, names) if v]
        except Exception as e:  # e.g. an abstraction value the model's types cannot parse
            result.violations = [(-1, [f"eval failed: {str(e)[:120]}"])]
    return result


def validate(make_adapter, model: LeanModel, traces: list, workers: int = 8) -> list[TraceResult]:
    """Validate traces in parallel; each worker thread gets its own adapter and data dir."""
    def work(chunk):
        adapter = make_adapter()
        try:
            return [validate_trace(adapter, model, t) for t in chunk]
        finally:
            adapter.close()

    chunks = [traces[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(workers) as pool:
        return [r for rs in pool.map(work, chunks) for r in rs]
