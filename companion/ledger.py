"""The constraint ledger: every constraint stated in the conversation, with where it was
said, its status, and its Lean predicate. Active entries are rendered into a generated Lean
module and compiled into the app's companion executable.

Statuses: `proposed` and `confirmed` are active (checked every turn); `superseded` and
`retired` are kept for the record; `unformalized` means the predicate did not typecheck and
the entry is not checked (its Lean error is kept).

An entry may also name the situation it is about (the circumstance in which it could be
broken: for "when X, then Y", it is X), as text and as a Lean predicate on transitions. The
companion only reports such an entry checked if its search actually reaches that situation;
a search that never gets there says nothing about the rule. For this, each situation is
rendered as a probe property, `<id>?reach`, that fails exactly where the situation occurs,
so the search's counterexample to the probe is a shortest trace that reaches it.
"""

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

ACTIVE = ("proposed", "confirmed")
REACH = "?reach"             # suffix of the probe property for an entry's situation


def is_probe(property_name: str) -> bool:
    return property_name.endswith(REACH)


@dataclass
class Entry:
    id: str
    text: str                    # the constraint, in the developer's words where possible
    predicate: str               # Lean term of the app's property type, e.g. `fun t => ...`
    kind: str = "state"          # "state" (about one state) or "transition" (about a step)
    status: str = "proposed"
    confidence: str = "high"     # "low" entries would get a confirmation question
    provenance: list = field(default_factory=list)   # [{"turn": n, "quote": "..."}]
    history: list = field(default_factory=list)      # earlier versions, with the turn they changed
    error: str | None = None     # Lean error if the predicate does not typecheck
    situation: str | None = None             # what must happen for the rule to be put to the test
    situation_predicate: str | None = None   # the same, as a Lean predicate on transitions

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    @property
    def last_turn(self) -> int:
        return max((p.get("turn", 0) for p in self.provenance), default=0)


@dataclass
class Ledger:
    path: Path
    turn: int = 0
    entries: list = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        path = Path(path)
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text())
        return cls(path, data.get("turn", 0), [Entry(**e) for e in data.get("entries", [])])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"turn": self.turn, "entries": [asdict(e) for e in self.entries]}, indent=1))

    def get(self, id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == id), None)

    def next_id(self) -> str:
        nums = [int(e.id[1:]) for e in self.entries if re.fullmatch(r"c\d+", e.id)]
        return f"c{max(nums, default=0) + 1}"

    def active(self) -> list:
        return [e for e in self.entries if e.active]

    # Changes, as an extractor proposes them ---------------------------------------------

    def apply(self, ops: list, turn: int) -> list:
        """Apply add / revise / retire operations. Returns a description of each change."""
        log = []
        for op in ops:
            kind = op.get("op")
            if kind == "add":
                e = Entry(id=self.next_id(), text=op["text"], predicate=op["predicate"],
                          kind=op.get("kind", "state"), confidence=op.get("confidence", "high"),
                          provenance=[{"turn": turn, "quote": op.get("quote", "")}],
                          situation=op.get("situation"), situation_predicate=op.get("situation_predicate"))
                self.entries.append(e)
                log.append(f"added {e.id}: {e.text}")
            elif kind == "revise" and (e := self.get(op.get("id", ""))):
                e.history.append({"turn": turn, "text": e.text, "predicate": e.predicate,
                                  "situation": e.situation, "situation_predicate": e.situation_predicate})
                e.text = op.get("text", e.text)
                e.predicate = op.get("predicate", e.predicate)
                e.situation = op.get("situation", e.situation)
                e.situation_predicate = op.get("situation_predicate", e.situation_predicate)
                e.kind = op.get("kind", e.kind)
                e.status, e.error = "proposed", None
                e.provenance.append({"turn": turn, "quote": op.get("quote", "")})
                log.append(f"revised {e.id}: {e.text}")
            elif kind in ("retire", "supersede") and (e := self.get(op.get("id", ""))):
                e.status = "retired" if kind == "retire" else "superseded"
                e.provenance.append({"turn": turn, "quote": op.get("quote", ""), "reason": op.get("reason", "")})
                log.append(f"{e.status} {e.id}: {op.get('reason', '')}")
            else:
                log.append(f"ignored operation: {json.dumps(op)[:200]}")
        return log


# Rendering into Lean --------------------------------------------------------------------

def _lean_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def render(ledger: Ledger, header: str, footer: str, prop_type: str) -> tuple[str, dict]:
    """The Lean module for the active entries that have not failed to typecheck, and the
    line range of each entry (for mapping Lean errors back to entries)."""
    lines = [l for l in header.rstrip("\n").split("\n")]
    lines += ["", "-- Generated from the constraint ledger by companion/ledger.py. Do not edit.", ""]
    ranges, names = {}, []
    for e in ledger.active():
        if e.error:
            continue
        start = len(lines) + 1
        body = e.predicate.strip().split("\n")
        lines.append(f"def {e.id} : {prop_type} :=")
        lines.append(f"  ⟨{_lean_string(e.id + ': ' + e.text)},")
        lines += ["    " + l for l in body]
        lines[-1] += "⟩"
        lines.append("")
        names.append(e.id)
        if e.situation_predicate:
            transition_type = prop_type.replace("Property", "Transition", 1)
            lines.append(f"def {e.id}_situation : {transition_type} → Bool :=")
            lines += ["  " + l for l in e.situation_predicate.strip().split("\n")]
            lines.append("")
            lines.append(f"def {e.id}_reach : {prop_type} := ⟨{_lean_string(e.id + REACH)}, fun t => !({e.id}_situation t)⟩")
            lines.append("")
            names.append(f"{e.id}_reach")
        ranges[e.id] = (start, len(lines))
    lines.append(f"def ledgerProperties : List ({prop_type}) := [{', '.join(names)}]")
    lines += ["", *footer.rstrip("\n").split("\n"), ""]
    return "\n".join(lines), ranges


def typecheck(ledger: Ledger, cfg) -> list:
    """Render the ledger and build its Lean module. An entry whose predicate does not
    typecheck becomes `unformalized`, with its error, and the module is rebuilt without it.
    Returns the ids that failed."""
    failed = []
    for _ in range(len(ledger.entries) + 1):
        text, ranges = render(ledger, cfg.lean_header, cfg.lean_footer, cfg.property_type)
        cfg.ledger_file.write_text(text)
        # The ledger module is generated, so it belongs to no Lake library: typecheck the
        # file directly against the built modules it imports.
        result = subprocess.run(["lake", "env", "lean", str(cfg.ledger_file.relative_to(cfg.lean_dir))],
                                cwd=cfg.lean_dir, capture_output=True, text=True)
        if result.returncode == 0:
            return failed
        errors = _errors_by_line(result.stdout + result.stderr, cfg.ledger_file.name)
        culprits = {id for id, (a, b) in ranges.items() for line in errors if a <= line <= b}
        if not culprits:          # an error outside any entry: nothing to blame, give up
            raise RuntimeError("ledger module does not build:\n" + (result.stdout + result.stderr)[-2000:])
        for id in culprits:
            e = ledger.get(id)
            e.status, e.error = "unformalized", "\n".join(m for l, m in errors.items() if ranges[id][0] <= l <= ranges[id][1])[:2000]
            failed.append(id)
    raise RuntimeError("ledger module does not build after removing failing entries")


def _errors_by_line(output: str, filename: str) -> dict:
    errors = {}
    # `lean FILE` reports "FILE:line:col: error: msg"; `lake build` reports "error: FILE:line:col: msg".
    # Lean 4.34 may tag the severity with a code: "error(lean.invalidField): msg".
    pattern = rf"(?:\S*{re.escape(filename)}:(\d+):\d+: error(?:\([^)]*\))?: |error: \S*{re.escape(filename)}:(\d+):\d+: )(.*?)(?=\n\S*\.lean:\d+:\d+: |\nerror|\nwarning|\Z)"
    for m in re.finditer(pattern, output, re.S):
        errors[int(m.group(1) or m.group(2))] = m.group(3).strip()
    return errors
