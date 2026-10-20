"""Adapter for booking.py: runs the model's actions on the real program and reads its
state back in the model's terms. Handles `move` too, which only the turn-2 model uses."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from companion import runner


class Adapter:
    def __init__(self, program):
        self.program = program
        root = os.environ.get("COMPANION_TMP")
        if root:
            Path(root).mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(dir=root))
        self.reset()

    def reset(self):
        shutil.rmtree(self.dir / "data", ignore_errors=True)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def args(self, action):
        if action == "list":
            return ["list"]
        (kind, a), = action.items()
        if kind == "book":
            return ["book", f"room-{a['room']}", a["start"], a["stop"]]
        if kind == "cancel":
            return ["cancel", a["id"]]
        return ["move", a["id"], a["start"], a["stop"]]

    def call(self, args):
        r = runner.run(self.program, ["--data-dir", str(self.dir / "data"), *map(str, args)])
        return r.code, json.loads(r.stdout)

    @staticmethod
    def view(b):
        return {"id": b["id"], "room": int(b["room"].removeprefix("room-")), "start": b["start"], "stop": b["end"]}

    def run(self, action):
        code, body = self.call(self.args(action))
        if code != 0:
            return {"error": {"category": body["error"]}}
        if action == "list":
            return {"bookings": {"bs": [self.view(b) for b in body]}}
        (kind, _), = action.items()
        if kind == "book":
            return {"booked": {"b": self.view(body)}}
        if kind == "cancel":
            return "cancelled"
        return {"moved": {"b": self.view(body)}}

    def observe(self):
        _, body = self.call(["list"])
        return {"bookings": [self.view(b) for b in body]}

    def describe(self, action):
        return "booking.py " + " ".join(map(str, self.args(action)))
