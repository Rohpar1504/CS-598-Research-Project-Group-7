"""`companion check`: after an agent turn, decide for every active constraint whether the
code keeps it.

1. Bring the model up to date (companion/sync.py): build it with the ledger's properties,
   run trace validation, and if the model no longer matches the code, or changed code is
   reached by no trace, have the LLM agent repair it (for a workspace with an agent).
   Property violations seen on validation's real runs are real, whatever the model says.
2. If the model matches the code: search every area of the model for violations, and replay
   each counterexample on the real code. Only a violation that reproduces counts. A
   counterexample the code does not reproduce shows the model is wrong there: the trace
   joins the validation set, and with an agent the model is repaired and searched again.
   If the model does not match the code, searching it would be meaningless: nothing is
   claimed checked, and the real violations are kept.
3. An entry that names its situation is claimed checked only if the search reaches that
   situation (its probe property, see ledger.py). Otherwise the search said nothing about
   it; with an agent, the model's search areas are widened (once per version of the
   entry) and the search runs again.
4. Evidence per entry: violated (with a reproduction), checked (to a depth, on so many
   traces), or unchecked (with the reason).
"""

import json
import time
from pathlib import Path

from companion import workspace
from companion.config import Config
from companion.lean import LeanModel
from companion.ledger import Ledger, is_probe
from companion.sync import add_validation_traces, build, repair, sync   # noqa: F401  (build: for callers)
from companion.trace_validation import validate_trace


def entry_id(property_name: str) -> str:
    return property_name.split(":", 1)[0]


def reproduction(cfg: Config, model: LeanModel, trace: list) -> tuple[list[str], set]:
    """The trace run once more on the real code, observing every step: its commands, and
    the ids of the entries it violates."""
    adapter = cfg.make_adapter()
    try:
        result = validate_trace(adapter, model, trace)
        describe = getattr(adapter, "describe", None) or (lambda a: json.dumps(a))
        return [describe(a) for a in trace], {entry_id(n) for _, ns in result.violations for n in ns
                                               if not is_probe(n)}
    finally:
        adapter.close()


def check(cfg: Config, log=print, agent=None, llm=None) -> dict:
    """Check every active constraint. With an `agent` (and a workspace), the model is
    repaired when it has fallen behind the code; `llm` repairs predicates the new model
    breaks."""
    start = time.time()
    if not cfg.program.exists():
        raise FileNotFoundError(f"the program to check does not exist: {cfg.program}")
    if cfg.workspace and not workspace.has_model(cfg):
        raise RuntimeError(f"{cfg.workspace} has no model yet: run `companion synthesize` first")
    repairs = []
    for attempt in range(2):
        s = sync(cfg, agent, llm, log)
        if s.repair:
            repairs.append(s.repair)
        v = s.validation
        model, results, mismatched = v.model, v.results, v.mismatched

        # Violations seen on real runs: keep the shortest offending prefix per entry.
        violated: dict[str, list] = {}
        for r in results:
            for step, names in r.violations:
                for n in names:
                    if is_probe(n):
                        continue
                    prefix = r.trace[: step + 1]
                    if step >= 0 and (entry_id(n) not in violated or len(prefix) < len(violated[entry_id(n)])):
                        violated[entry_id(n)] = prefix
        found_by = {i: "trace validation" for i in violated}

        # Validation may have used the model's state where it did not read the real one, so
        # every violation is confirmed by a replay that observes each step.
        repro = {}
        for i, trace in list(violated.items()):
            commands, confirmed = reproduction(cfg, model, trace)
            if i in confirmed:
                repro[i] = commands
            else:
                del violated[i], found_by[i]

        model_errors, wrong_traces, reached = set(), [], {}
        depth = cfg.check.get("search_depth", 8)
        if v.matches:
            areas = model.areas()
            for area in areas:
                for r in model.check(area, depth):
                    i = entry_id(r["property"])
                    ce = r["counterexample"]
                    if is_probe(r["property"]):
                        i = i[: -len("?reach")]
                        if ce and (i not in reached or len(ce) < reached[i][1]):
                            reached[i] = (area, len(ce))
                        continue
                    if not ce or (i in violated and len(violated[i]) <= len(ce)):
                        continue
                    commands, on_code = reproduction(cfg, model, ce)
                    if i in on_code:
                        violated[i], found_by[i], repro[i] = ce, "search", commands
                    else:
                        model_errors.add(i)
                        wrong_traces.append(ce)
            log(f"search: {len(areas)} areas to depth {depth}")
        ledger = Ledger.load(cfg.ledger_path)
        unreached = [e for e in ledger.active() if e.situation_predicate and e.id not in reached
                     and e.id not in violated] if v.matches else []
        if unreached:
            log(f"the search never reaches the situation of {', '.join(e.id for e in unreached)}")
        again = False
        if wrong_traces:
            add_validation_traces(cfg, wrong_traces)
            log(f"{len(wrong_traces)} counterexamples of the model are not reproduced by the code; "
                "they join the validation traces")
            again = True          # validate again: the model now mismatches, and gets repaired
        ask = _not_yet_asked(cfg, unreached)
        if ask and agent is not None and cfg.workspace and attempt == 0:
            _, _, info = repair(cfg, agent, llm, v, None, log, unreached=ask)
            repairs.append(info)
            _mark_asked(cfg, ask)
            again = True
        if not (again and agent is not None and cfg.workspace and attempt == 0):
            break

    repaired = any(r["fixed"] for r in repairs)
    failed_repair = bool(repairs) and not v.matches
    entries = []
    for e in ledger.entries:
        if e.status == "unformalized":
            ev = {"evidence": "unchecked", "why": "its Lean predicate does not typecheck", "error": e.error}
        elif not e.active:
            continue
        elif e.id in violated:
            ev = {"evidence": "violated", "found_by": found_by[e.id],
                  "reproduction": repro[e.id], "trace": violated[e.id],
                  "new_this_turn": e.last_turn >= ledger.turn}
        elif v.error:
            ev = {"evidence": "unchecked", "why": "the model does not work" + (" and could not be repaired" if failed_repair else "")}
        elif mismatched:
            ev = {"evidence": "unchecked", "why": "the model no longer matches the code"
                  + (", and repairing it did not succeed" if failed_repair else "")}
        elif e.id in model_errors:
            ev = {"evidence": "unchecked", "why": "the model reports a violation the code does not reproduce"}
        elif e.situation_predicate and e.id not in reached:
            ev = {"evidence": "unchecked", "why": f"the search never reaches the situation it is about: {e.situation}"}
        else:
            ev = {"evidence": "checked",
                  "detail": f"no violation in {len(results)} validated traces, nor within {depth} steps "
                            f"of search in the areas {', '.join(model.areas())}"}
            if e.id in reached:
                ev["detail"] += f"; its situation is reached in area {reached[e.id][0]} in {reached[e.id][1]} steps"
            if s.gaps:
                ev["detail"] += f"; {len(s.gaps)} changed statements are reached by no trace"
        entries.append({"id": e.id, "text": e.text, "quote": e.provenance[-1].get("quote", "") if e.provenance else "",
                        "turn": e.last_turn, **ev})

    st = v.stats
    report = {
        "turn": ledger.turn, "program": str(cfg.program), "seconds": round(time.time() - start, 1),
        "model_matches_code": v.matches,
        "model_error": v.error,
        "model_repaired": repaired,
        "repairs": repairs,
        "unreached_changes": s.gaps,
        "validation": {"traces": len(results), "steps": sum(len(r.trace) for r in results),
                       "reused": st.reused, "steps_run": st.steps_run,
                       "program_runs": st.program_calls, "seconds": st.seconds,
                       "mismatches": len(mismatched),
                       "first_mismatch": _mismatch_summary(mismatched[0].first_mismatch) if mismatched else None},
        "entries": entries,
    }
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "report.json").write_text(json.dumps(report, indent=1))
    return report


def _not_yet_asked(cfg: Config, entries: list) -> list:
    """The entries whose unreached situation the agent has not been asked about, in their
    current version (asking again each turn would cost a repair every turn)."""
    path = cfg.state_dir / "situations-asked.json"
    asked = json.loads(path.read_text()) if path.exists() else {}
    return [e for e in entries if asked.get(e.id) != e.situation_predicate]


def _mark_asked(cfg: Config, entries: list) -> None:
    path = cfg.state_dir / "situations-asked.json"
    asked = json.loads(path.read_text()) if path.exists() else {}
    asked.update({e.id: e.situation_predicate for e in entries})
    path.write_text(json.dumps(asked))


def _mismatch_summary(m: dict) -> dict:
    keys = ("step", "action", "expected_out", "real_out")
    return {k: m.get(k) for k in keys if k in m}


def for_agent(report: dict, limit: int = 3) -> str | None:
    """The message for the coding agent, or None if there is nothing to report. Violations
    of constraints stated before this turn are regressions; those stated this turn are
    requests not yet met."""
    bad = [e for e in report["entries"] if e["evidence"] == "violated"]
    if not bad:
        return None
    lines = ["The code no longer keeps constraints the developer stated. Each was reproduced on the "
             "real program, starting from its initial state:", ""]
    for n, e in enumerate(bad[:limit], 1):
        label = "not yet met (stated this turn)" if e.get("new_this_turn") else f"regression (stated in turn {e['turn']})"
        lines.append(f"{n}. [{label}] {e['text']}")
        if e["quote"]:
            lines.append(f'   The developer said: "{e["quote"]}"')
        lines.append("   Reproduce:")
        lines += [f"     {s}" for s in e["reproduction"]]
        lines.append("   After the last command the constraint no longer holds.")
        lines.append("")
    if len(bad) > limit:
        lines.append(f"({len(bad) - limit} more in the companion's report.)")
    return "\n".join(lines).rstrip()


MARKS = {"checked": ("✓", "32"), "violated": ("✗", "31"), "unchecked": ("?", "33")}


def _painter(color: bool):
    return (lambda code, s: f"\033[{code}m{s}\033[0m") if color else (lambda code, s: s)


def summary(report: dict, color: bool = False) -> str:
    """The report, for people."""
    paint = _painter(color)
    v = report["validation"]
    out = [paint("1", f"Turn {report['turn']} · {Path(report['program']).name}")]
    if report.get("model_error"):
        out.append(paint("31", "The model does not work: " + report["model_error"].splitlines()[0][:160]))
    elif report["model_matches_code"]:
        out.append(f"The model matches the code on {v['traces']} traces ({report['seconds']} s).")
    else:
        out.append(paint("31", f"The model does not match the code: {v['mismatches']} of {v['traces']} "
                               "traces differ, so nothing can be checked."))
    for r in report.get("repairs") or []:
        why = (f"so that its search reaches the situations of {', '.join(r['asked_about_situations'])}"
               if r.get("asked_about_situations") else "to follow the code")
        cost = f", ${r['cost_usd']:.2f}" if r.get("cost_usd") is not None else ""
        out.append(f"The LLM updated the model {why}"
                   + ("" if r["fixed"] else paint("31", ", without success"))
                   + f" ({r['rounds']} round{'s' if r['rounds'] != 1 else ''}, {r['seconds']} s{cost}).")
    gaps = report.get("unreached_changes")
    if gaps:
        by_file: dict = {}
        for g in gaps:
            by_file.setdefault(g["file"], []).append(g["line"])
        where = ", ".join(f"{f}:{min(ls)}-{max(ls)}" if len(ls) > 1 else f"{f}:{ls[0]}" for f, ls in by_file.items())
        out.append(paint("33", f"Warning: {len(gaps)} changed statements ({where}) are run by no validation trace, "
                               "so the model may not cover them and the results below may miss violations."))
    out.append("")
    for e in report["entries"]:
        mark, code = MARKS[e["evidence"]]
        out.append(f"{paint(code, mark)} {e['id']}  {e['text']}")
        if e["evidence"] == "violated":
            when = "stated this turn, not met yet" if e.get("new_this_turn") else f"regression: stated in turn {e['turn']}"
            out.append(f"     Violated ({when}). Reproduced on the real program:")
            out += [f"       {s}" for s in e["reproduction"]]
        elif e["evidence"] == "unchecked":
            out.append(f"     Not checked: {e['why']}.")
    counts = [f"{sum(e['evidence'] == k for e in report['entries'])} {k}" for k in MARKS
              if any(e["evidence"] == k for e in report["entries"])]
    out += ["", ", ".join(counts) + "." if counts else "No constraints yet."]
    return "\n".join(out)


def status(cfg: Config, color: bool = False) -> str:
    """The ledger, with each constraint's result in the last check."""
    paint = _painter(color)
    ledger = Ledger.load(cfg.ledger_path)
    path = cfg.state_dir / "report.json"
    last = {e["id"]: e["evidence"] for e in json.loads(path.read_text())["entries"]} if path.exists() else {}
    if not ledger.entries:
        return "No constraints yet."
    out = [paint("1", f"Turn {ledger.turn} · {len(ledger.entries)} constraints")]
    for e in ledger.entries:
        mark, code = MARKS.get(last.get(e.id), (" ", "0"))
        status = "" if e.status == "confirmed" else f"  [{e.status}]"
        out.append(f"{paint(code, mark)} {e.id}  {e.text}  (turn {e.last_turn}){status}")
    if last:
        out.append("\n✓ checked, ✗ violated, ? not checked, in the last `companion check`.")
    return "\n".join(out)
