"""Command line of the companion.

  python -m companion new WORKSPACE --program PATH [--app NAME]
  python -m companion --config CONFIG [--program PATH] COMMAND

CONFIG is a workspace directory or a config file (such as apps/forge/companion.json).

  new WORKSPACE               make a workspace for an application, with no model yet
  synthesize                  have the LLM agent write the workspace's model and adapter
  validate [--quick]          trace-validate the model against the program (no repair)
  init [--from LEDGER.json]   start a session: empty ledger, or a copy of an existing one
  ledger                      show the ledger
  build                       typecheck the ledger's predicates and build the executable
  check [--no-repair]         check every active constraint against the code (writes report.json);
                              repairs a workspace's model first if it has fallen behind the code
  extract MESSAGE...          turn a developer message into ledger changes (needs an LLM backend)
  hook user-prompt|stop       Claude Code hooks: read the hook's JSON on stdin
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from companion.config import Config
from companion.ledger import Ledger


def main() -> int:
    ap = argparse.ArgumentParser(prog="companion")
    ap.add_argument("--config", help="a workspace directory or a config file")
    ap.add_argument("--program", help="the application under test (overrides the config)")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new"); p.add_argument("workspace"); p.add_argument("--app")
    p.add_argument("--program", dest="new_program", help="the application under test")
    sub.add_parser("synthesize")
    p = sub.add_parser("validate"); p.add_argument("--quick", action="store_true")
    p = sub.add_parser("init"); p.add_argument("--from", dest="source")
    sub.add_parser("ledger")
    sub.add_parser("build")
    p = sub.add_parser("check"); p.add_argument("--no-repair", action="store_true")
    p = sub.add_parser("extract"); p.add_argument("message", nargs="+")
    p = sub.add_parser("hook"); p.add_argument("event", choices=["user-prompt", "stop"])
    args = ap.parse_args()

    if args.command == "new":
        from companion import workspace
        program = args.new_program or args.program
        if not program:
            ap.error("new needs --program")
        cfg = workspace.create(Path(args.workspace), Path(program), app=args.app)
        print(f"workspace for {cfg.app} in {cfg.workspace}; next: --config {cfg.workspace} synthesize")
        return 0
    if not args.config:
        ap.error("--config is required")
    cfg = Config.load(args.config, args.program)
    log = lambda m: print(m, file=sys.stderr)

    if args.command == "synthesize":
        from companion.llm import agent_backend, backend
        from companion.sync import synthesize
        agent = agent_backend(cfg)
        if agent is None:
            ap.error("synthesize needs a workspace and Claude Code (set COMPANION_CLAUDE if it is not found)")
        result = synthesize(cfg, agent, backend(cfg), log)
        print(json.dumps({k: v for k, v in result.items() if k not in ("synthesis", "repair")}, indent=1))
        return 0 if result["matches"] else 2
    elif args.command == "validate":
        from companion.sync import validate_main
        if not cfg.workspace:
            ap.error("validate works on a workspace")
        return validate_main(cfg.workspace, args.quick)
    elif args.command == "init":
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        if args.source:
            shutil.copy(args.source, cfg.ledger_path)
        else:
            Ledger(cfg.ledger_path).save()
        (cfg.state_dir / "messages.jsonl").unlink(missing_ok=True)
        print(f"session started in {cfg.state_dir}")
    elif args.command == "ledger":
        for e in Ledger.load(cfg.ledger_path).entries:
            print(f"{e.id:4} {e.status:12} turn {e.last_turn:2}  {e.text}")
    elif args.command == "build":
        from companion.sync import build
        ledger = Ledger.load(cfg.ledger_path)
        failed = build(cfg, ledger)
        ledger.save()
        print("built" + (f"; not formalized: {', '.join(failed)}" if failed else ""))
    elif args.command == "check":
        from companion.check import check, for_agent, summary
        from companion.llm import agent_backend, backend
        agent = None if args.no_repair else agent_backend(cfg)
        report = check(cfg, log=log, agent=agent, llm=backend(cfg) if agent else None)
        print(summary(report))
        message = for_agent(report)
        if message:
            print("\n--- message for the agent ---\n" + message)
    elif args.command == "extract":
        from companion.extract import extract
        print("\n".join(extract(cfg, " ".join(args.message))))
    elif args.command == "hook":
        from companion.hooks import user_prompt, stop
        event = json.loads(sys.stdin.read() or "{}")
        out = user_prompt(cfg, event) if args.event == "user-prompt" else stop(cfg, event)
        if out:
            print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
