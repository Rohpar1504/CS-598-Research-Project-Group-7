"""`companion extract`: turn a developer message into changes to the constraint ledger.

The LLM sees the app's vocabulary (the Lean types of observations, actions and outputs,
and the helper functions), the active constraints, and the message. It answers with
operations: add, revise or retire. New and revised predicates are typechecked in Lean; a
predicate that fails gets one repair round with Lean's error, and otherwise stays
`unformalized` (recorded, not checked).
"""

import json
import re

from companion.config import Config
from companion.ledger import Ledger, typecheck
from companion.llm import backend as default_backend
from companion.sync import build_model_modules

SYSTEM = """You maintain the constraint ledger of a coding companion. A developer is building an \
application with a coding agent. Your job is to record the constraints the developer states or \
agrees to about the application's behavior, as Lean predicates, so they can be checked after every \
change.

Record only rules about observable behavior that should keep holding: what the state may or may \
not contain, what an action must or must not do, which requests are rejected, what outputs look \
like. Do not record one-off tasks ("add a list command"), implementation choices, or opinions. A \
message may state no constraint at all; then return no operations.

When a message changes or contradicts an earlier constraint, revise or retire that constraint \
instead of adding a conflicting one.

Answer with a single JSON object and nothing else:
{"ops": [
  {"op": "add", "text": "<the rule in one plain sentence>", "quote": "<the developer's words>",
   "kind": "state" | "transition", "confidence": "high" | "low", "predicate": "<Lean term>",
   "situation": "<when the rule is put to the test, in one plain sentence>",
   "situation_predicate": "<Lean term>"},
  {"op": "revise", "id": "<existing id>", "text": "...", "quote": "...", "predicate": "<Lean term>",
   "situation": "...", "situation_predicate": "<Lean term>"},
  {"op": "retire", "id": "<existing id>", "quote": "...", "reason": "..."}
]}

The situation is the circumstance in which the rule could be broken, so that checking it means \
something: for a rule "when X happens, Y", it is X ("a blueprint that an allocation uses is \
deleted"); for a rule about every state, it is a step that would break the rule if the code were \
wrong ("an allocation is unbound or released while it has an assignment"). The companion uses it \
to make sure its search really exercises the rule, so make it as specific as the rule needs, and \
something the application can actually do. Its predicate has the same form as the rule's and is \
true exactly on transitions where the situation occurs.

A predicate is a Lean 4 term of type `Companion.Property`'s predicate, i.e. a function \
`fun t => ...` returning Bool, where `t : Companion.Transition Action Out Obs` has fields \
`t.before : Obs`, `t.action : Action`, `t.out : Out` and `t.after : Obs`. A state constraint \
speaks about `t.after`. Use only the definitions shown in the vocabulary; prefer Bool operations \
(`==`, `&&`, `||`, `!`, `List.all`, `List.any`). Use "low" confidence when you had to guess what \
the developer meant."""


def vocabulary(cfg: Config) -> str:
    parts = []
    for path in cfg.vocabulary_files():
        parts.append(f"-- {path.name}\n{path.read_text()}")
    return "\n\n".join(parts)


def namespace(cfg: Config) -> str:
    return next((l.split()[1] for l in cfg.lean_header.splitlines() if l.startswith("namespace ")), "")


def prompt(cfg: Config, ledger: Ledger, message: str) -> str:
    active = [{"id": e.id, "text": e.text, "kind": e.kind, "predicate": e.predicate,
               "situation": e.situation, "situation_predicate": e.situation_predicate} for e in ledger.active()]
    return (f"VOCABULARY (Lean, namespace {namespace(cfg)}):\n\n{vocabulary(cfg)}\n\n"
            f"ACTIVE CONSTRAINTS:\n{json.dumps(active, indent=1)}\n\n"
            f"TURN {ledger.turn}. THE DEVELOPER'S MESSAGE:\n{message}\n")


def repair_prompt(cfg: Config, ledger: Ledger, ids: list) -> str:
    broken = [{"id": e.id, "text": e.text, "predicate": e.predicate, "situation": e.situation,
               "situation_predicate": e.situation_predicate, "lean_error": e.error}
              for e in ledger.entries if e.id in ids]
    return (f"VOCABULARY (Lean, namespace {namespace(cfg)}):\n\n{vocabulary(cfg)}\n\n"
            f"These predicates (a rule's, or its situation's) do not typecheck. Fix them, keeping their "
            f'meaning. Answer with {{"ops": [{{"op": "revise", "id": ..., "predicate": ..., '
            f'"situation_predicate": ...}}]}} only.\n\n{json.dumps(broken, indent=1)}\n')


def parse_ops(text: str) -> list:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in the response: {text[:300]}")
    return json.loads(m.group(0)).get("ops", [])


def extract(cfg: Config, message: str, llm=None, turn: int | None = None) -> list:
    """Apply the constraints in `message` to the ledger, as said in `turn` (by default, a new
    turn). Returns a description of each change."""
    llm = llm or default_backend(cfg)
    if llm is None:
        raise RuntimeError("no LLM backend available")
    build_model_modules(cfg)              # predicates are typechecked against the model
    ledger = Ledger.load(cfg.ledger_path)
    if turn is None:
        ledger.turn += 1
        turn = ledger.turn
    log = ledger.apply(parse_ops(llm.complete(prompt(cfg, ledger, message), SYSTEM)), turn)
    failed = typecheck(ledger, cfg)
    if failed:
        log.append(f"did not typecheck: {', '.join(failed)}; asking for a repair")
        repair_predicates(cfg, ledger, failed, llm)
        still = typecheck(ledger, cfg)
        log.append(f"still not formalized: {', '.join(still)}" if still else "repaired")
    ledger.save()
    return log


def repair_predicates(cfg: Config, ledger: Ledger, ids: list, llm) -> list:
    """One repair round for predicates that do not typecheck: the LLM sees Lean's errors and
    the vocabulary, and may only revise those predicates. The caller typechecks again."""
    ops = [op for op in parse_ops(llm.complete(repair_prompt(cfg, ledger, ids), SYSTEM))
           if op.get("op") == "revise" and op.get("id") in ids]
    for op in ops:            # a repair changes the formalization, not what the developer said
        op.pop("text", None)
        op.pop("quote", None)
    return _revise_in_place(ledger, ops)


def _revise_in_place(ledger: Ledger, ops: list) -> list:
    log = []
    for op in ops:
        e = ledger.get(op["id"])
        e.history.append({"turn": ledger.turn, "text": e.text, "predicate": e.predicate,
                          "situation_predicate": e.situation_predicate, "error": e.error})
        e.predicate = op.get("predicate", e.predicate)
        e.situation_predicate = op.get("situation_predicate", e.situation_predicate)
        e.status, e.error = "proposed", None
        log.append(f"repaired the predicate of {e.id}")
    return log
