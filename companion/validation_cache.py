"""Cache of real execution records for trace validation, invalidated by code changes.

Each record holds what the real program did on one trace (its outputs and observations, in
the model's terms) and which lines of the program's source the trace executed. When the
program's source changes, a record is reused only if no change can affect that trace:

- A change inside a function (its body, signature or decorators) invalidates every trace that
  executed any line of that function. Whole functions, not single lines: an inserted check
  between two lines a trace never ran can still be on its path (a new `elif` before an
  `else` it takes).
- A change outside any function (a constant, an import, a class attribute) invalidates every
  trace: module-level code runs once when the program is loaded and is in no trace's lines.
- Adding a whole new top-level function or class, under a new name, invalidates nothing
  until some existing code that traces run is changed to use it.
- A file that does not parse, or disappears, invalidates every trace that touched it.

This assumes the program's behavior depends only on its code and the trace (not on other
files, the clock or the environment). A changed adapter invalidates everything, since
records are in the adapter's terms. A changed model or ledger does not: reused records are
compared with the current model on every run.
"""

import ast
import difflib
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from companion.trace_validation import Record

ALL = "all"


def _functions(source: str):
    """(first line, last line) of every function and method, decorators included; None if
    the source does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            spans.append((start, node.end_lineno))
    return spans


def _top_level_names(source: str) -> dict:
    """Top-level function and class names -> (first line, last line)."""
    names = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            names[node.name] = (start, node.end_lineno)
    return names


def _only_new_definitions(new_lines: list, j1: int, j2: int, new_defs: dict, old_names: set) -> bool:
    """True if new lines j1+1..j2 are blank, comments, or inside top-level definitions whose
    names are new."""
    fresh = [span for name, span in new_defs.items() if name not in old_names]
    for n in range(j1 + 1, j2 + 1):
        text = new_lines[n - 1].strip()
        if text and not text.startswith("#") and not any(a <= n <= b for a, b in fresh):
            return False
    return True


def changed_old_lines(old: str | None, new: str | None):
    """The lines of `old` (1-based) whose traces must be rerun, or ALL."""
    if old is None or old == new:
        return set()
    if new is None:
        return ALL
    old_funcs, new_funcs = _functions(old), _functions(new)
    if old_funcs is None or new_funcs is None:
        return ALL
    a, b = old.splitlines(), new.splitlines()
    new_defs, old_names = _top_level_names(new), set(_top_level_names(old))
    changed = set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert" and _only_new_definitions(b, j1, j2, new_defs, old_names):
            continue
        # The changed region of the old file: lines i1+1..i2, or the gap after line i1.
        lo, hi = (i1 + 1, i2) if i2 > i1 else (i1, i1 + 1)
        inside = [(s, e) for s, e in old_funcs if s <= lo and hi <= e]
        if not inside:
            return ALL
        s, e = min(inside, key=lambda span: span[1] - span[0])   # the innermost function
        changed.update(range(s, e + 1))
    return changed


def line_map(old: str | None, new: str | None) -> dict:
    """Old line number -> new line number, for lines in unchanged stretches."""
    if old is None or new is None:
        return {}
    m = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(),
                                                      autojunk=False).get_opcodes():
        if tag == "equal":
            m.update({i1 + k + 1: j1 + k + 1 for k in range(i2 - i1)})
    return m


class ValidationCache:
    def __init__(self, path: Path, sources: list, adapter_file: Path | None):
        self.path = Path(path)
        self.sources = {str(Path(p).resolve()): Path(p).read_text() for p in sources if Path(p).exists()}
        self.adapter_hash = hashlib.sha256(Path(adapter_file).read_bytes()).hexdigest() if adapter_file else None
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        old = data.get("sources", {})
        self.old_records = data.get("records", {}) if data.get("adapter_hash") == self.adapter_hash else {}
        files = set(old) | set(self.sources)
        self.changed = {f: changed_old_lines(old.get(f), self.sources.get(f)) for f in files}
        self.maps = {f: line_map(old.get(f), self.sources.get(f)) for f in files}
        self.records = {}

    def get(self, key: str, mode: str) -> Record | None:
        """The cached record for a trace, in the current source's line numbers, if no code
        change can affect it."""
        r = self.old_records.get(key)
        if not r or r["footprint"] is None or (mode == "all" and r["mode"] != "all"):
            return None
        fp = {}
        for f, lines in r["footprint"].items():
            changed = self.changed.get(f, ALL)
            if changed == ALL or not changed.isdisjoint(lines):
                return None
            fp[f] = sorted(self.maps[f][l] for l in lines)
        return Record(r["outs"], r["obs"], r["initial"], fp, r["mode"])

    def covers(self, traces: list, mode: str) -> bool:
        """True if every trace has a usable record (nothing needs to run)."""
        return all(self.get(json.dumps(t, sort_keys=True), mode) is not None for t in traces)

    def put(self, key: str, record: Record) -> None:
        self.records[key] = asdict(record)

    def save(self) -> None:
        """Keep the records of this run, in the current source's line numbers."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"adapter_hash": self.adapter_hash, "sources": self.sources,
                                         "records": self.records}))
