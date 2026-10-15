"""Running the program under test.

`run(program, args, stdin)` runs the program once, like `python PROGRAM ARGS` with `stdin`,
and returns its exit code and output. Adapters call it for every action and observation, so
its cost dominates trace validation.

By default a Python program is run by forking a copy of this process in which the program's
module is already loaded, and calling its `main(argv)` in the child: about 1.5 ms instead of
about 20 ms for a new Python process. Each run is still a separate process that starts from
the module as loaded (any state lives on disk), so the program behaves as it would when
started fresh. The module is reloaded when its source file changes.

A new process is used instead when the program cannot be run this way: it is not Python, it
has no top-level `main(argv)`, or it does not load (a syntax error, say: the new process then
shows the program's real failure). Whether there is a `main` is decided from the source before
anything is loaded, so a plain script is never executed inside this process. Loading a module
does run its module-level code once here; programs whose module-level code has side effects
beyond definitions should set `COMPANION_RUNNER=subprocess`. `COMPANION_RUNNER=subprocess` forces new processes, for programs
whose module-level code must run on every start.

Inside `with recording() as rec:`, each run also records which lines of the program's code it
executed (`rec.lines`: file -> set of line numbers). `rec.complete` is False if some run could
not be recorded (it used a new process). `calls` counts the runs of this process.
"""

import ast
import importlib.util
import io
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
import traceback
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

calls = 0
_loaded: dict = {}                  # program -> (mtimes, module, code objects) or None
_lock = threading.Lock()
_recording = None

warnings.filterwarnings("ignore", message=".*use of fork\\(\\) may lead to deadlocks.*")


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str


@dataclass
class Recording:
    lines: dict = field(default_factory=dict)
    complete: bool = True


@contextmanager
def recording():
    global _recording
    previous, _recording = _recording, Recording()
    try:
        yield _recording
    finally:
        _recording = previous


def run(program, args: list, stdin: str | None = None, timeout: float = 60) -> Result:
    """Run the program once. Raises subprocess.TimeoutExpired if it runs past `timeout`."""
    global calls
    calls += 1
    program = Path(program).resolve()
    loaded = None if os.environ.get("COMPANION_RUNNER") == "subprocess" else _load(program)
    if loaded is None:
        if _recording is not None:
            _recording.complete = False
        r = subprocess.run([sys.executable, str(program), *map(str, args)], input=stdin or "",
                           capture_output=True, text=True, timeout=timeout)
        return Result(r.returncode, r.stdout, r.stderr)
    return _fork_run(loaded, [str(program), *map(str, args)], stdin or "", timeout)


# Loading ------------------------------------------------------------------------------------

def _module_file(program: Path) -> Path | None:
    for c in ([program] if program.suffix == ".py" else [program.with_suffix(".py"), program.parent / f"{program.name}.py"]):
        if c.exists():
            return c
    return None


def _load(program: Path):
    """(module, code objects) for the program, loaded once and again after it changes; None
    if it cannot be run by fork."""
    source = _module_file(program)
    if source is None:
        return None
    stamp = (source.stat().st_mtime_ns, program.stat().st_mtime_ns if program.exists() else 0)
    with _lock:
        cached = _loaded.get(program)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        try:
            if not _defines_main(source):
                raise ValueError("no top-level main")
            spec = importlib.util.spec_from_file_location(f"_program_{abs(hash((str(source), stamp)))}", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded = (module, _code_objects(module)) if callable(getattr(module, "main", None)) else None
        except BaseException:          # does not load: run it as a new process instead
            loaded = None
        _loaded[program] = (stamp, loaded)
        return loaded


def _defines_main(source: Path) -> bool:
    """True if the file defines a top-level function `main` (checked without running it)."""
    tree = ast.parse(source.read_text())
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main" for n in tree.body)


def _code_objects(module) -> list:
    """Every code object defined in the program's module: functions, methods, nested
    functions."""
    found, seen = [], set()

    def visit(code):
        if id(code) not in seen:
            seen.add(id(code))
            found.append(code)
            for c in code.co_consts:
                if hasattr(c, "co_code"):
                    visit(c)

    for value in list(vars(module).values()):
        for f in ([value, *vars(value).values()] if isinstance(value, type) else [value]):
            code = getattr(f, "__code__", None)
            if code is not None and code.co_filename == module.__file__:
                visit(code)
    return found


# Running by fork ----------------------------------------------------------------------------

def _trace_lines(codes: list, lines: set) -> None:
    """In the child: record each line of the program's code that runs, once. Events are
    enabled on the program's own code objects only; enabling them globally would make Python
    instrument every function loaded in this process."""
    mon = sys.monitoring
    try:
        mon.use_tool_id(mon.COVERAGE_ID, "companion")
    except ValueError:
        return

    def on_line(code, line):
        lines.add((code.co_filename, line))
        return mon.DISABLE

    mon.register_callback(mon.COVERAGE_ID, mon.events.LINE, on_line)
    for code in codes:
        mon.set_local_events(mon.COVERAGE_ID, code, mon.events.LINE)


def _child(loaded, argv: list, stdin: str, out_w: int, err_w: int, cov_w: int | None) -> None:
    module, codes = loaded
    code, lines = 1, set()
    try:
        os.dup2(out_w, 1)
        os.dup2(err_w, 2)
        sys.stdout = open(1, "w", closefd=False)
        sys.stderr = open(2, "w", closefd=False)
        sys.stdin = io.StringIO(stdin)
        sys.argv = argv
        if cov_w is not None:
            _trace_lines(codes, lines)
        code = module.main(argv[1:])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            if cov_w is not None:
                with os.fdopen(cov_w, "w") as f:
                    json.dump(sorted(lines), f)
        finally:
            os._exit(code if isinstance(code, int) else 0)


def _fork_run(loaded, argv: list, stdin: str, timeout: float) -> Result:
    record = _recording
    pipes = {name: os.pipe() for name in (["out", "err", "cov"] if record else ["out", "err"])}
    pid = os.fork()
    if pid == 0:
        for r, _ in pipes.values():
            os.close(r)
        _child(loaded, argv, stdin, pipes["out"][1], pipes["err"][1], pipes["cov"][1] if record else None)
    for _, w in pipes.values():
        os.close(w)
    data = _read_all({name: r for name, (r, _) in pipes.items()}, timeout, pid, argv)
    _, status = os.waitpid(pid, 0)
    if record:
        for file, line in json.loads(data["cov"] or "[]"):
            record.lines.setdefault(file, set()).add(line)
    return Result(os.waitstatus_to_exitcode(status), data["out"], data["err"])


def _read_all(fds: dict, timeout: float, pid: int, argv: list) -> dict:
    chunks = {name: [] for name in fds}
    sel = selectors.DefaultSelector()
    for name, fd in fds.items():
        sel.register(fd, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout
    try:
        while sel.get_map():
            left = deadline - time.monotonic()
            if left <= 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                raise subprocess.TimeoutExpired(argv, timeout)
            for key, _ in sel.select(left):
                block = os.read(key.fd, 65536)
                if block:
                    chunks[key.data].append(block)
                else:
                    sel.unregister(key.fd)
                    os.close(key.fd)
    finally:
        for key in list(sel.get_map().values()):
            os.close(key.fd)
        sel.close()
    return {name: b"".join(c).decode(errors="replace") for name, c in chunks.items()}
