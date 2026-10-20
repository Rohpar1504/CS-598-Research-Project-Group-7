"""Command line of the companion.

The companion keeps its workspace in `.companion/` next to the program it checks, and finds
it from the current directory (or a parent), as git finds `.git`.

  companion init PROGRAM [--model M]    set up .companion/ for PROGRAM (no model yet); M is the
                                        LLM for everything (default claude-haiku-5-5)
  companion synthesize                  have the LLM agent write the model and adapter
  companion tell MESSAGE...             a developer message -> constraints (needs an LLM)
  companion check [--no-llm] [-v]       check every constraint against the code, after an agent turn
  companion status                      the constraints and their last result
  companion reset [--from LEDGER.json]  start over: empty constraints, or a copy of a ledger
  companion hook user-prompt|stop       Claude Code hooks: read the hook's JSON on stdin

  companion validate [--quick]          trace-validate the model against the program (debugging)
  companion build                       typecheck the constraints and build the model (debugging)

Every command takes `--workspace PATH` to use another workspace, or a fixed config such as
apps/forge/companion.json (also COMPANION_WORKSPACE).
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from companion.config import Config
from companion.ledger import Ledger

DIR = ".companion"


def find_workspace(given: str | None) -> Path | None:
    if given or os.environ.get("COMPANION_WORKSPACE"):
        return Path(given or os.environ["COMPANION_WORKSPACE"])
    for d in [Path.cwd(), *Path.cwd().parents]:
        if (d / DIR / "companion.json").exists():
            return d / DIR
    return None


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", help="a workspace directory or a fixed config file")
    common.add_argument("-v", "--verbose", action="store_true", help="show each step")
    ap = argparse.ArgumentParser(prog="companion", description="Checks the code against the "
                                 "developer's constraints after every agent turn.")
    ap.add_argument("--workspace", dest="top_workspace", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")
    p = sub.add_parser("init", parents=[common], help="set up .companion/ for a program")
    p.add_argument("program"); p.add_argument("--app")
    p.add_argument("--model", default="claude-haiku-5-5", help="the LLM (default: claude-haiku-5-5)")
    sub.add_parser("synthesize", parents=[common], help="have the LLM write the model and adapter")
    p = sub.add_parser("tell", parents=[common], help="a developer message -> constraints")
    p.add_argument("message", nargs="+")
    p = sub.add_parser("check", parents=[common], help="check every constraint against the code")
    p.add_argument("--no-llm", action="store_true", help="do not let the LLM update the model")
    p.add_argument("--program", help="check another version of the program")
    sub.add_parser("status", parents=[common], help="the constraints and their last result")
    p = sub.add_parser("reset", parents=[common], help="start over with no constraints")
    p.add_argument("--from", dest="source", help="start from a copy of this ledger")
    p = sub.add_parser("hook", parents=[common], help="Claude Code hooks")
    p.add_argument("event", choices=["user-prompt", "stop"])
    p = sub.add_parser("validate", parents=[common], help="trace validation only (debugging)")
    p.add_argument("--quick", action="store_true")
    sub.add_parser("build", parents=[common], help="build the model and constraints (debugging)")
    args = ap.parse_args()
    args.workspace = args.workspace or args.top_workspace
    say = lambda m: print(m, file=sys.stderr, flush=True)
    log = say if args.verbose else (lambda m: None)

    if args.command == "init":
        from companion import workspace
        ws = Path(args.workspace) if args.workspace else Path(args.program).resolve().parent / DIR
        cfg = workspace.create(ws, Path(args.program), app=args.app, agent={"model": args.model},
                               check={"llm": {"model": args.model}})
        print(f"Created {_shown(cfg.workspace)} for {cfg.program.name}, using {args.model}.\n"
              f"Next: `companion synthesize` has the LLM write the model ({cfg.namespace}), "
              f"or write lean/{cfg.namespace}.lean and adapter.py there yourself.")
        return 0
    found = find_workspace(args.workspace)
    if found is None:
        ap.error(f"no {DIR}/ here or in a parent directory: run `companion init PROGRAM` first")
    cfg = Config.load(found, getattr(args, "program", None))

    if args.command == "synthesize":
        from companion.llm import agent_backend, backend
        from companion.sync import synthesize
        agent = agent_backend(cfg)
        if agent is None:
            ap.error("synthesize needs a workspace and Claude Code (set COMPANION_CLAUDE if it is not found)")
        agent.progress = say
        result = synthesize(cfg, agent, backend(cfg), say)
        took = f"{round((result['seconds'] or 0) / 60, 1)} min" + (f", ${result['cost_usd']:.2f}" if result["cost_usd"] else "")
        if result["matches"]:
            print(f"\nThe model matches the program on {result['traces']} traces ({took}).")
        else:
            print(f"\nThe model does not match the program: "
                  + (result["error"].splitlines()[0] if result["error"] else
                     f"{result['mismatches']} of {result['traces']} traces differ") + f" ({took}).")
        return 0 if result["matches"] else 2
    elif args.command == "validate":
        from companion.sync import validate_main
        if not cfg.workspace:
            ap.error("validate works on a workspace")
        return validate_main(cfg.workspace, args.quick)
    elif args.command == "reset":
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        if args.source:
            shutil.copy(args.source, cfg.ledger_path)
        else:
            Ledger(cfg.ledger_path).save()
        (cfg.state_dir / "messages.jsonl").unlink(missing_ok=True)
        n = len(Ledger.load(cfg.ledger_path).entries)
        print(f"Started over with {n} constraint{'s' if n != 1 else ''}.")
    elif args.command == "status":
        from companion.check import status
        print(status(cfg, color=sys.stdout.isatty()))
    elif args.command == "build":
        from companion.sync import build
        ledger = Ledger.load(cfg.ledger_path)
        failed = build(cfg, ledger)
        ledger.save()
        print("built" + (f"; not formalized: {', '.join(failed)}" if failed else ""))
    elif args.command == "check":
        from companion.check import check, summary
        from companion.llm import agent_backend, backend
        agent = None if args.no_llm else agent_backend(cfg)
        if agent is not None:
            agent.progress = say
        report = check(cfg, log=log, agent=agent, llm=backend(cfg) if agent else None)
        print(summary(report, color=sys.stdout.isatty()))
        return 1 if any(e["evidence"] == "violated" for e in report["entries"]) else 0
    elif args.command == "tell":
        from companion.extract import extract
        print("\n".join(extract(cfg, " ".join(args.message))) or "No constraints in that message.")
    elif args.command == "hook":
        from companion.hooks import user_prompt, stop
        event = json.loads(sys.stdin.read() or "{}")
        out = user_prompt(cfg, event) if args.event == "user-prompt" else stop(cfg, event)
        if out:
            print(json.dumps(out))
    return 0


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd())) + "/"
    except ValueError:
        return str(path) + "/"


if __name__ == "__main__":
    sys.exit(main())
