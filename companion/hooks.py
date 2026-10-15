"""Claude Code hooks. Each reads the hook's JSON event and returns the JSON to print (or None).

- UserPromptSubmit: record the developer's message as a new turn. Extraction waits until the
  agent stops, so the developer is never kept waiting.
- Stop: extract constraints from the turn's messages, run `check` (which first repairs the
  model if the turn's edits left it behind the code), and if a constraint is violated on the
  real code, block the stop and give the agent the reproductions. The retry budget is one:
  when the agent stops again after being blocked (`stop_hook_active`), it is let through.

Companion failures never block the agent; they are reported to the developer instead.
See apps/forge/claude-settings.json for how the hooks are registered.
"""

import json
import traceback

from companion.check import check, for_agent, summary
from companion.config import Config
from companion.extract import extract
from companion.ledger import Ledger
from companion.llm import agent_backend, backend


def _messages_path(cfg: Config):
    return cfg.state_dir / "messages.jsonl"


def user_prompt(cfg: Config, event: dict):
    ledger = Ledger.load(cfg.ledger_path)
    ledger.turn += 1
    ledger.save()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(_messages_path(cfg), "a") as fh:
        fh.write(json.dumps({"turn": ledger.turn, "prompt": event.get("prompt", ""), "extracted": False}) + "\n")
    return None


def stop(cfg: Config, event: dict):
    if event.get("stop_hook_active"):
        return None
    notes = []
    try:
        notes += _extract_pending(cfg)
        report = check(cfg, log=notes.append, agent=agent_backend(cfg), llm=backend(cfg))
    except Exception as e:   # never block the agent because the companion failed
        return {"systemMessage": f"companion failed: {e}\n{traceback.format_exc()[-800:]}"}
    message = for_agent(report)
    out = {"systemMessage": "companion: " + summary(report) + ("\n" + "\n".join(notes) if notes else "")}
    if message:
        out.update({"decision": "block", "reason": message})
    return out


def _extract_pending(cfg: Config) -> list:
    path = _messages_path(cfg)
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    pending = [r for r in rows if not r["extracted"]]
    if not pending:
        return []
    llm = backend(cfg)
    if llm is None:
        return ["extraction skipped: no LLM backend"]
    notes = []
    for r in pending:
        notes += extract(cfg, r["prompt"], llm, turn=r["turn"])
        r["extracted"] = True
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return notes
