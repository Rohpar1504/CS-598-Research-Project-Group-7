# Task: write an executable Lean model of a program, and an adapter for it

The program is `<<PROGRAM>>`. Its source code is in `<<PROGRAM_DIR>>` (read it there; do not change it). There is no specification: read the code to learn what the program does, how it is run, and where it keeps its state.

Write two things in this directory:

1. **A Lean model** in `lean/<<NS>>.lean` (the root module; it may import modules you put under `lean/<<NS>>/`). It describes how each command changes the program's state and what it returns, following what the code does, including which requests it rejects and with which error.
2. **An adapter** in `adapter.py`. It runs an abstract action of your model on the real program, and translates the program's output and state back into your model's terms.

You are done when `<<PYTHON>> validate.py` builds your model and reports **0 mismatching traces**. It runs the same action sequences on the real program (through your adapter) and on your model and compares them step by step. Use `<<PYTHON>> validate.py --quick` while iterating. Model as much of the program as you can: every command that reads or changes stored state, with its error responses. If you leave something out, say what at the end.

**Follow the code, not what you think the program should do.** If the code looks wrong, model what it does anyway. Your model is used to check rules the developer stated about the program, and those checks only mean something if the model does what the code does.

Work only inside this directory, and read only files in it and in the program's directory. Do not edit `validate.py`, `lean/Companion/`, `lean/Main.lean`, `lean/ModelMain.lean`, `lean/Ledger.lean`, `lean/lakefile.toml` or `state/`.

## The Lean side

In namespace `<<NS>>`, define types named exactly `State`, `Action`, `Out` and `Obs` (rules written later refer to them by these names), and

```lean
def app : Companion.App State Action Out Obs := { model, areas, validationAlphabet, properties := [] }
```

`lean/Companion/Core.lean` and `lean/Companion/Cli.lean` give you (read them):

```lean
structure Companion.Model (σ α ο θ : Type) where
  init    : σ                 -- the empty state
  step    : σ → α → σ × ο     -- a rejected request is an output (an error), not a failure
  observe : σ → θ             -- everything the program's read commands show
  inScope : σ → Bool := fun _ => true

structure Companion.Area (σ α : Type) where   -- a small search scope
  name : String; alphabet : List α; bound : σ → Bool

structure Companion.App (σ α ο θ : Type) where
  model : Model σ α ο θ; areas : List (Area σ α); validationAlphabet : List α
  properties : List (Property α ο θ)          -- leave this []
```

Your types need `BEq, Hashable` (state) and `BEq, ToJson, FromJson` (actions, outputs, observations), all derivable.

**Abstraction.** The model works with small values, and the adapter maps them to real ones:
- A resource's id is its position in that resource's creation order: the first one created is 0. The adapter remembers the real id (e.g. a UUID) of each. An id that was never created should map to a well-formed id that does not exist, so the program answers "not found".
- Names and other values are naturals in the model, mapped by the adapter to strings like `name-3`. Where the program treats some values specially (a reserved name, a magic category), give them their own abstract values.
- Leave out what the model cannot predict (timestamps, random ids). Where the program orders results by such a value, the adapter should put them in a predictable order before comparing.
- Errors: model them as an output carrying the program's error category, and follow the code's order of checks.
- `observe` must be computable by the adapter from the program's **read commands** (list, get, ...), never from its data files.

**Areas** are what search explores exhaustively, so keep each small: group commands that interact, and bound the number of resources (2 or 3 of each). Every command that changes state should be in some area. `validationAlphabet` is a wider set of actions for random traces: include every command, with valid and invalid inputs.

## The Python side

`adapter.py` must define:

```python
class Adapter:
    def __init__(self, program: Path): ...   # program = <<PROGRAM>>; make a fresh, empty state
    def reset(self) -> None: ...             # back to the empty state, forget ids
    def run(self, action) -> dict: ...       # run one action, return the JSON of your Out value
    def observe(self) -> dict: ...           # read the whole state, return the JSON of your Obs value
    def close(self) -> None: ...             # delete everything it created
    def describe(self, action) -> str: ...   # the command line an action runs, as a developer would
                                             # type it (reproductions of bugs are shown this way)
    # Optional:
    def snapshot(self): ...                  # copy the current state (e.g. the data dir) ...
    def restore(self, snap) -> None: ...     # ... put it back ...
    def release(self, snap) -> None: ...     # ... and delete the copy. Makes validation much faster.
```

`action` is the JSON that Lean's derived `toJson` gives for your `Action`, and `run`/`observe` must return exactly the JSON your model's `toJson` gives for `Out`/`Obs`. Derived JSON looks like this: a structure is an object with its field names; a constructor with no arguments is its name as a string (`"list"`); a constructor with arguments is `{"name": {"argName": value}}`; `Option` is `null` or the value; `List` is an array.

Keep the adapter in this one file. Put temporary files under the directory in the environment variable `COMPANION_TMP` if it is set (else the system's temporary directory). For a Python program, run it with `companion.runner.run(program, args, stdin)`, which returns `.code`, `.stdout` and `.stderr` (it is much faster than starting a new process, and it records which code each run executes). For other programs, use `subprocess`. If some real output cannot be expressed in your types, put `companion.abstraction.inexpressible("what you expected", raw)` in its place, so that the step counts as a mismatch.

## A tiny example (a different program: a notes CLI, namespace `Notes`)

```lean
import Companion.Cli
open Lean
namespace Notes

structure State where
  notes : List (Nat × Nat) := []   -- (id, title); id = position in creation order
  next  : Nat := 0
deriving BEq, Hashable, Repr

inductive Action where
  | add (title : Nat)
  | delete (id : Nat)
  | list
deriving BEq, Repr, ToJson, FromJson

structure NoteView where
  id : Nat
  title : Nat
deriving BEq, Repr, ToJson, FromJson

inductive Out where
  | note (n : NoteView)
  | notes (ns : List NoteView)
  | deleted
  | error (category : String)
deriving BEq, Repr, ToJson, FromJson

structure Obs where
  notes : List NoteView
deriving BEq, Repr, ToJson, FromJson

def observe (s : State) : Obs := { notes := s.notes.map fun (i, t) => { id := i, title := t } }

def step (s : State) : Action → State × Out
  | .add title =>
    if s.notes.any (·.2 == title) then (s, .error "conflict")
    else ({ notes := s.notes ++ [(s.next, title)], next := s.next + 1 }, .note { id := s.next, title })
  | .delete id =>
    if s.notes.any (·.1 == id) then ({ s with notes := s.notes.filter (·.1 != id) }, .deleted)
    else (s, .error "not_found")
  | .list => (s, .notes (observe s).notes)

def model : Companion.Model State Action Out Obs := { init := {}, step, observe }

def app : Companion.App State Action Out Obs :=
  { model
    areas := [{ name := "notes", alphabet := [.add 0, .add 1, .delete 0, .delete 1, .list],
                bound := fun s => s.next ≤ 3 }]
    validationAlphabet := [.add 0, .add 1, .add 2, .delete 0, .delete 1, .delete 5, .list]
    properties := [] }

end Notes
```

```python
import json, os, shutil, tempfile
from pathlib import Path
from companion import runner

class Adapter:
    def __init__(self, program):
        self.program = program
        self.dir = Path(tempfile.mkdtemp(dir=os.environ.get("COMPANION_TMP")))
        self.reset()

    def reset(self):
        shutil.rmtree(self.dir / "data", ignore_errors=True)
        self.ids = []                                  # real id of abstract note k

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, args, payload=None):
        r = runner.run(self.program, ["--data-dir", self.dir / "data", *args],
                       json.dumps(payload) if payload is not None else None)
        return r.code, json.loads(r.stdout)

    def real_id(self, k):
        return self.ids[k] if k < len(self.ids) else f"missing-{k}"

    def view(self, n):
        return {"id": self.ids.index(n["id"]), "title": int(n["title"].removeprefix("title-"))}

    def run(self, action):
        if action == "list":
            code, body = self.call(["list"])
            return {"notes": {"ns": [self.view(n) for n in body]}}
        (kind, a), = action.items()
        if kind == "add":
            code, body = self.call(["add"], {"title": f"title-{a['title']}"})
            if code != 0:
                return {"error": {"category": body["error"]}}
            self.ids.append(body["id"])
            return {"note": {"n": self.view(body)}}
        if kind == "delete":
            code, body = self.call(["delete", self.real_id(a["id"])])
            return "deleted" if code == 0 else {"error": {"category": body["error"]}}

    def observe(self):
        code, body = self.call(["list"])
        return {"notes": sorted((self.view(n) for n in body), key=lambda v: v["id"])}
```

At the end, report: which commands you modeled, what you left out and why, and the last output of `<<PYTHON>> validate.py`.
