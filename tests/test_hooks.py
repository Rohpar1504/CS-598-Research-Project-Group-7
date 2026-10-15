"""The Claude Code hooks (companion.hooks).

What we want: each developer message starts a turn and is recorded; when the agent stops,
the turn's messages become constraints and the code is checked; the agent is blocked, with
reproductions, exactly when a stated constraint is broken on the real program, and at most
once per turn; a failure of the companion never blocks the agent.

The LLM is replaced by fixed answers (COMPANION_FIXTURE).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import EXAMPLE_LEDGER, quick_config, requires_forge


@requires_forge
class HooksTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.fixture = self.dir / "answers.json"
        self.fixture.write_text(json.dumps([{"ops": []}] * 5))
        self.old = os.environ.get("COMPANION_FIXTURE")
        os.environ["COMPANION_FIXTURE"] = str(self.fixture)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("COMPANION_FIXTURE", None)
        else:
            os.environ["COMPANION_FIXTURE"] = self.old

    def ledger_of(self, ids):
        data = json.loads(EXAMPLE_LEDGER.read_text())
        data["entries"] = [e for e in data["entries"] if e["id"] in ids]
        path = self.dir / "ledger.json"
        path.write_text(json.dumps(data))
        return path

    def test_a_developer_message_starts_a_turn_and_is_recorded(self):
        from companion.hooks import user_prompt
        from companion.ledger import Ledger
        cfg = quick_config(self.dir)
        self.assertIsNone(user_prompt(cfg, {"prompt": "rooms must not be double booked"}))
        self.assertEqual(Ledger.load(cfg.ledger_path).turn, 1)
        rows = [json.loads(l) for l in (cfg.state_dir / "messages.jsonl").read_text().splitlines()]
        self.assertEqual(rows, [{"turn": 1, "prompt": "rooms must not be double booked", "extracted": False}])

    def test_the_agent_is_blocked_with_reproductions_when_a_stated_rule_is_broken(self):
        from companion.hooks import stop
        cfg = quick_config(self.dir, ledger=EXAMPLE_LEDGER)
        out = stop(cfg, {"stop_hook_active": False})
        self.assertEqual(out["decision"], "block")
        self.assertIn("Reproduce:", out["reason"])
        self.assertIn("don't let a blueprint be deleted while allocations still use it", out["reason"])

    def test_the_agent_may_stop_when_every_stated_rule_holds(self):
        from companion.hooks import stop
        cfg = quick_config(self.dir, ledger=self.ledger_of({"c1", "c2", "c3", "c5"}))
        out = stop(cfg, {"stop_hook_active": False})
        self.assertNotIn("decision", out)
        self.assertIn("checked", out["systemMessage"])

    def test_the_agent_is_blocked_at_most_once_per_turn(self):
        from companion.hooks import stop
        cfg = quick_config(self.dir, ledger=EXAMPLE_LEDGER)
        self.assertIsNone(stop(cfg, {"stop_hook_active": True}))

    def test_a_failure_of_the_companion_never_blocks_the_agent(self):
        from companion.hooks import stop
        cfg = quick_config(self.dir, program=self.dir / "no-such-program", ledger=EXAMPLE_LEDGER)
        out = stop(cfg, {"stop_hook_active": False})
        self.assertNotIn("decision", out)
        self.assertIn("companion failed", out["systemMessage"])

    def test_the_turns_messages_become_constraints_before_the_check(self):
        from companion.hooks import stop, user_prompt
        from companion.ledger import Ledger
        broken = json.loads(EXAMPLE_LEDGER.read_text())["entries"][8]          # c9, which forge breaks
        self.fixture.write_text(json.dumps([{"ops": [{"op": "add", "text": broken["text"],
                                                      "quote": "blueprints in use can't be deleted",
                                                      "predicate": broken["predicate"]}]}]))
        cfg = quick_config(self.dir)
        user_prompt(cfg, {"prompt": "blueprints in use can't be deleted"})
        out = stop(cfg, {"stop_hook_active": False})
        entry = Ledger.load(cfg.ledger_path).get("c1")
        self.assertEqual((entry.text, entry.last_turn), (broken["text"], 1))
        self.assertEqual(out["decision"], "block")
        self.assertIn("not yet met", out["reason"])      # stated this very turn


if __name__ == "__main__":
    unittest.main()
