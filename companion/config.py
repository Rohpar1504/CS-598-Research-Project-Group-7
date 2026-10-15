"""Per-app configuration of the companion, read from a JSON file.

Two kinds of config:

- A **workspace** (`"workspace": true`, made by `companion new`): the companion's own
  directory for one application, holding a model and an adapter that an LLM writes and keeps
  up to date (companion/workspace.py describes the layout). Everything except the program is
  derived from the workspace's layout.
- A **fixed** config (apps/forge/companion.json): a model and adapter that live elsewhere and
  are maintained by hand, such as the hand-built forge model in lean/Forge. Every path is
  given. The companion cannot repair such a model; it only reports that it no longer matches.

Relative paths are resolved against the config's `root` (by default its own directory).
"""

import hashlib
import importlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path

PROPERTY_TYPE = "Companion.Property Action Out Obs"


@dataclass
class Config:
    root: Path                  # project root; relative paths below are resolved against it
    app: str
    lean_dir: Path              # the Lake project
    ledger_module: str          # e.g. Forge.Ledger
    ledger_file: Path           # the generated Lean file for that module
    lean_header: str            # imports, namespace and opens for the generated module
    lean_footer: str
    property_type: str          # e.g. Companion.Property Action Out Obs
    exe: str                    # the Lake executable built with the ledger's properties
    adapter: str                # "module:Class", or "path/to/file.py:Class"
    program: Path               # the application under test
    state_dir: Path             # ledger, messages, reports and caches
    vocabulary: list            # Lean files shown to the extractor (types and helpers)
    check: dict = field(default_factory=dict)
    sources: list | None = None # the program's source files (default: the program and the .py files next to it)
    workspace: Path | None = None   # set for a workspace config
    namespace: str | None = None    # a workspace's Lean namespace (and root module)
    agent: dict = field(default_factory=dict)   # the LLM agent that writes and repairs the model

    @classmethod
    def load(cls, path: Path, program: str | None = None) -> "Config":
        path = Path(path).resolve()
        if path.is_dir():
            path = path / "companion.json"
        d = json.loads(path.read_text())
        root = (path.parent / d.get("root", ".")).resolve()
        r = lambda p: (root / p).resolve()
        prog = Path(program).resolve() if program else r(d["program"])
        if d.get("workspace"):
            ws, ns = path.parent, d["namespace"]
            return cls(root=ws, app=d["app"], lean_dir=ws / "lean", ledger_module="Ledger",
                       ledger_file=ws / "lean" / "Ledger.lean",
                       lean_header=f"import {ns}\n\nnamespace {ns}\n\nopen Companion", lean_footer=f"end {ns}",
                       property_type=PROPERTY_TYPE, exe="companion-app", adapter=f"{ws / 'adapter.py'}:Adapter",
                       program=prog, state_dir=ws / "state", vocabulary=[], check=d.get("check", {}),
                       sources=d.get("sources"), workspace=ws, namespace=ns, agent=d.get("agent", {}))
        return cls(root=root, app=d["app"], lean_dir=r(d["lean_dir"]), ledger_module=d["ledger_module"],
                   ledger_file=r(d["ledger_file"]), lean_header=d["lean_header"], lean_footer=d["lean_footer"],
                   property_type=d["property_type"], exe=d["exe"], adapter=d["adapter"],
                   program=prog, state_dir=r(d["state_dir"]), vocabulary=[r(v) for v in d.get("vocabulary", [])],
                   check=d.get("check", {}), sources=d.get("sources"))

    @property
    def exe_path(self) -> Path:
        return self.lean_dir / ".lake" / "build" / "bin" / self.exe

    @property
    def model_exe(self) -> str:
        """An executable of the model without the ledger's properties: it builds even when
        the ledger does not typecheck (as during a repair that renames things)."""
        return "companion-model" if self.workspace else self.exe

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.json"

    def model_imports(self) -> list:
        """The modules the generated ledger module imports."""
        return [l.split()[1] for l in self.lean_header.splitlines() if l.startswith("import ")]

    def vocabulary_files(self) -> list:
        """The Lean files that define the model's types and helpers. A workspace's are
        whatever the model currently consists of."""
        if not self.workspace:
            return self.vocabulary
        lean = self.lean_dir
        return [p for p in [lean / f"{self.namespace}.lean", *sorted((lean / self.namespace).rglob("*.lean"))]
                if p.exists()]

    def source_files(self) -> list:
        """The program's source files."""
        if self.sources:
            files = set()
            for pattern in self.sources:
                files |= {p.resolve() for p in self.program.parent.glob(pattern) if p.is_file()}
            return sorted(files)
        return sorted({self.program, *(p.resolve() for p in self.program.parent.glob("*.py"))})

    def adapter_file(self) -> Path:
        target = self.adapter.rsplit(":", 1)[0]
        if target.endswith(".py"):
            return Path(target)
        return Path(importlib.import_module(target).__file__)

    def make_adapter(self):
        target, cls = self.adapter.rsplit(":", 1)
        module = _load_file(Path(target)) if target.endswith(".py") else importlib.import_module(target)
        return getattr(module, cls)(self.program)


_modules: dict = {}


def _load_file(path: Path):
    """A Python file loaded as a module, again whenever its content changes (an agent may
    edit an adapter between two runs)."""
    data = path.read_bytes()
    key = (str(path), hashlib.sha256(data).hexdigest())
    if key not in _modules:
        spec = importlib.util.spec_from_file_location(f"_companion_adapter_{key[1][:16]}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _modules[key] = module
    return _modules[key]
