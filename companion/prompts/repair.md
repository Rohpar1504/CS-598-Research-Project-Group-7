# Task: bring the Lean model and adapter up to date with the program

This directory holds an executable Lean model of the program `<<PROGRAM>>` (`lean/<<NS>>.lean` and the modules under `lean/<<NS>>/`) and an adapter that runs the model's actions on the program (`adapter.py`). The model is meant to do exactly what the program's code does. The program's code has changed since the model last matched it, and the model must now follow the new code.

You are done when `<<PYTHON>> validate.py` builds the model and reports **0 mismatching traces** (`--quick` runs a smaller set while you iterate). Read the program's code in `<<PROGRAM_DIR>>` (do not change it).

**Follow the code, not what you think the program should do.** If the new code looks wrong, even if it looks like a bug, model what it does anyway. The model is used to check rules the developer stated, and a model that "fixes" the code would hide exactly the bugs those checks are for.

Change as little as you need. The developer's rules (below) refer to the model's types, fields and constructors by name: keep existing names, and only rename or remove something if the program itself no longer has it. When the program gains a command or a new case, add it to the model, the adapter, an area (if it changes state) and the `validationAlphabet`, so that validation traces reach it.

Work only inside this directory, and read only files in it and in the program's directory. Do not edit `validate.py`, `lean/Companion/`, `lean/Main.lean`, `lean/ModelMain.lean`, `lean/Ledger.lean`, `lean/lakefile.toml` or `state/`.

<<SECTIONS>>

At the end, say in a few sentences what you changed and why, and give the last line of `<<PYTHON>> validate.py`.
