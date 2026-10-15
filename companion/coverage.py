"""Changed code that no validation trace reaches.

Trace validation can only notice that the model has fallen behind the code where its traces
run the code. When the agent adds a command or a new branch, the model knows nothing of it,
no trace exercises it, and validation still passes. So after each validation, the program's
source is compared with its version when the model was last brought up to date (the
baseline), and every changed statement that no trace executed is reported: either the model
must be extended to reach it, or someone must decide that it is not reachable through the
application's interface.

Statements are the unit, not lines: a statement spanning several lines is executed if any
of its lines ran (Python records only some of them), and a compound statement (`if`, `for`,
...) owns only its header lines. Only statements inside functions count: module-level code
runs when the program is loaded, outside any trace, and a change there makes every trace run
again anyway (see validation_cache.py).
"""

import ast
import difflib
from pathlib import Path


def _statements(source: str) -> list | None:
    """(own lines, first line) of every statement inside a function; None if the source does
    not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    out = []

    def visit(node, in_function: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt) and in_function:
                own = _own_lines(child)
                if own:
                    out.append((own, child.lineno))
            visit(child, in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))
    visit(tree, False)
    return out


def _own_lines(stmt: ast.stmt) -> set:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
        return set()                                       # a docstring
    bodies = [getattr(stmt, f) for f in ("body", "orelse", "finalbody", "handlers", "cases")
              if isinstance(getattr(stmt, f, None), list) and getattr(stmt, f)]
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.lineno}                               # a nested definition: its def line
    if bodies:
        first = min(b[0].lineno for b in bodies)
        return set(range(stmt.lineno, max(stmt.lineno, first - 1) + 1))
    return set(range(stmt.lineno, stmt.end_lineno + 1))


def changed_lines(old: str, new: str) -> set:
    """Lines of `new` (1-based) that are new or changed relative to `old`."""
    out = set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(),
                                                      autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            out.update(range(j1 + 1, j2 + 1))
    return out


def uncovered_changes(baseline: dict, current: dict, coverage: dict | None, limit: int = 200) -> list | None:
    """The changed statements of the program (source text `current`, path -> text, against
    `baseline`) that no trace executed (`coverage`: path -> executed lines). Each is
    {"file", "line", "text"}. None if coverage is unknown (some run was not recorded)."""
    if coverage is None:
        return None
    gaps = []
    for path, new in sorted(current.items()):
        old = baseline.get(path)
        if old is None or old == new:
            continue
        stmts = _statements(new)
        if stmts is None:
            continue
        ran = set(coverage.get(path, ()))
        lines = new.splitlines()
        seen = set()
        for line in sorted(changed_lines(old, new)):
            owners = [(own, first) for own, first in stmts if line in own]
            if not owners:
                continue                                   # blank, comment, `else:`, module level
            own, first = min(owners, key=lambda o: len(o[0]))
            if first in seen or own & ran:
                continue
            seen.add(first)
            gaps.append({"file": Path(path).name, "line": first, "text": lines[first - 1].strip()[:160]})
            if len(gaps) >= limit:
                return gaps
    return gaps
