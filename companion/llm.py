"""LLM backends for the companion's own calls.

Two kinds of call:
- a completion (`complete(prompt, system) -> str`), for turning messages into constraints;
- an agent run (`run(prompt, cwd, add_dirs, log) -> dict`), for writing and repairing the
  model and adapter: the LLM works in the workspace with tools, reads the program's code,
  and runs trace validation itself until the model matches.

`ClaudeCLI` and `ClaudeAgent` run Claude Code headless (`claude -p`) with the user's login.
They load no settings files (`--setting-sources ""`), so the companion's hooks, which live in
settings, never fire on the companion's own calls. (`--bare` would also skip plugin hooks, but
it does not use the login: it needs an API key.)
`Fixture` replays canned completions, for tests without an LLM.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in sorted(Path.home().glob(".vscode*/extensions/anthropic.claude-code-*/resources/native-binary/claude")):
        return str(p)
    return None


class ClaudeCLI:
    def __init__(self, binary: str | None = None, model: str | None = None, budget_usd: float = 0.5):
        self.binary = binary or os.environ.get("COMPANION_CLAUDE") or find_claude()
        if not self.binary:
            raise RuntimeError("no claude binary found; set COMPANION_CLAUDE")
        self.model, self.budget = model, budget_usd

    def complete(self, prompt: str, system: str) -> str:
        cmd = [self.binary, "-p", "--setting-sources", "", "--tools", "", "--output-format", "json",
               "--no-session-persistence", "--max-budget-usd", str(self.budget), "--system-prompt", system]
        if self.model:
            cmd += ["--model", self.model]
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude failed ({result.returncode}): {(result.stderr or result.stdout)[-1000:]}")
        if data.get("is_error"):
            raise RuntimeError(f"claude error: {data.get('result')}")
        return data["result"]


class ClaudeAgent:
    """Claude Code as an agent with file and shell tools, working in a directory."""

    TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]

    def __init__(self, binary: str | None = None, model: str | None = None, effort: str | None = None,
                 budget_usd: float = 5.0, timeout: float = 3600):
        self.binary = binary or os.environ.get("COMPANION_CLAUDE") or find_claude()
        if not self.binary:
            raise RuntimeError("no claude binary found; set COMPANION_CLAUDE")
        self.model, self.effort, self.budget, self.timeout = model, effort, budget_usd, timeout

    def run(self, prompt: str, cwd: Path, add_dirs: list = (), log: Path | None = None) -> dict:
        """Run the agent on `prompt` in `cwd` (it may also read `add_dirs`). Returns
        {"ok", "seconds", "cost_usd", "turns", "result"}; the transcript goes to `log`."""
        cmd = [self.binary, "-p", "--setting-sources", "", "--permission-mode", "acceptEdits",
               "--output-format", "stream-json", "--verbose", "--no-session-persistence",
               "--max-budget-usd", str(self.budget)]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        for d in add_dirs:
            cmd += ["--add-dir", str(d)]
        cmd += ["--allowedTools", *self.TOOLS]
        log = Path(log) if log else Path(cwd) / "agent.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        with open(log, "w") as fh:
            try:
                proc = subprocess.run(cmd, input=prompt, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, text=True,
                                      timeout=self.timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = None
        result = {}
        for line in log.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result = event
        return {"ok": code == 0 and not result.get("is_error"), "seconds": round(time.time() - start),
                "cost_usd": result.get("total_cost_usd"), "turns": result.get("num_turns"),
                "result": (result.get("result") or ("timed out" if code is None else ""))[-3000:]}


class Fixture:
    """Returns the responses in a JSON list, one per call."""

    def __init__(self, path: Path):
        self.responses = json.loads(Path(path).read_text())

    def complete(self, prompt: str, system: str) -> str:
        if not self.responses:
            raise RuntimeError("fixture has no responses left")
        r = self.responses.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def agent_backend(cfg):
    """The agent that writes and repairs a workspace's model: None for a config without a
    workspace, in tests (COMPANION_FIXTURE), when COMPANION_AGENT=none, or when Claude Code
    cannot be found."""
    if not cfg.workspace or os.environ.get("COMPANION_FIXTURE") or os.environ.get("COMPANION_AGENT") == "none":
        return None
    a = cfg.agent
    try:
        return ClaudeAgent(model=a.get("model"), effort=a.get("effort"), budget_usd=a.get("budget_usd", 5.0))
    except RuntimeError:
        return None


def backend(cfg):
    """The backend the config or the environment selects: COMPANION_FIXTURE=<file> for
    tests, otherwise Claude Code if it can be found, otherwise None."""
    if os.environ.get("COMPANION_FIXTURE"):
        return Fixture(Path(os.environ["COMPANION_FIXTURE"]))
    llm = cfg.check.get("llm", {}) if isinstance(cfg.check, dict) else {}
    try:
        return ClaudeCLI(model=llm.get("model"), budget_usd=llm.get("budget_usd", 0.5))
    except RuntimeError:
        return None
