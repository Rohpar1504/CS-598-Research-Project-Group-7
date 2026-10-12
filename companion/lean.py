"""Calls into a compiled Lean model executable. Everything is exchanged as JSON."""

import json
import subprocess
from pathlib import Path


class LeanModel:
    def __init__(self, exe: Path, variant: str = "reference"):
        self.exe = str(exe)
        self.variant = variant  # which model of the code to use (see Forge/Variants.lean)

    def _run(self, *args: str, payload=None):
        result = subprocess.run(
            [self.exe, *args],
            input=None if payload is None else json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def alphabet(self, scope: str) -> list:
        return self._run("alphabet", scope)

    def paths(self, scope: str, depth: int, limit: int) -> list:
        return self._run("paths", scope, str(depth), str(limit))

    def replay(self, trace: list) -> dict:
        """{"initial": obs, "steps": [{"out": ..., "obs": ...}, ...]}"""
        return self._run("replay", self.variant, payload=trace)

    def check(self, depth: int) -> list:
        return self._run("check", str(depth), self.variant)

    def eval(self, transitions: list) -> list[list[str]]:
        """Names of the properties each transition violates."""
        return self._run("eval", payload=transitions)
