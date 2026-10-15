"""Turning developer messages into ledger changes (companion.extract).

These tests replace the LLM with fixed answers, so they check the pipeline around it: what
we want is that each stated constraint is recorded with the developer's words and the turn,
that a predicate which does not typecheck gets one repair round with Lean's error and is
otherwise kept but not checked, and that a message changing an earlier rule revises or
retires it. (How good the LLM's answers are is evaluated separately, in RQ1.)
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import quick_config, requires_forge

GOOD = "fun t => t.after.allocations.all fun a => (a.status == \"bound\") == a.assignment.isSome"
BAD = "fun t => t.after.allocationz.length == 0"


class Answers:
    """An LLM stand-in that gives fixed answers and records the prompts it was given."""

    def __init__(self, *answers):
        self.answers, self.prompts = [json.dumps(a) for a in answers], []

    def complete(self, prompt, system):
        self.prompts.append(prompt)
        return self.answers.pop(0)


@requires_forge
class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.cfg = quick_config(Path(tempfile.mkdtemp()))

    def ledger(self):
        from companion.ledger import Ledger
        return Ledger.load(self.cfg.ledger_path)

    def test_a_stated_constraint_is_recorded_with_the_developers_words_and_turn(self):
        from companion.extract import extract
        llm = Answers({"ops": [{"op": "add", "text": "Bound exactly when assigned.", "quote": "bound means assigned",
                                "kind": "state", "predicate": GOOD}]})
        extract(self.cfg, "bound means assigned, ok?", llm)
        entry = self.ledger().get("c1")
        self.assertEqual((entry.status, entry.error), ("proposed", None))
        self.assertEqual(entry.provenance, [{"turn": 1, "quote": "bound means assigned"}])
        self.assertIn("bound means assigned, ok?", llm.prompts[0])

    def test_a_predicate_that_does_not_typecheck_gets_one_repair_with_leans_error(self):
        from companion.extract import extract
        llm = Answers({"ops": [{"op": "add", "text": "rule", "quote": "q", "predicate": BAD}]},
                      {"ops": [{"op": "revise", "id": "c1", "predicate": GOOD}]})
        extract(self.cfg, "a message", llm)
        self.assertEqual(len(llm.prompts), 2)
        self.assertIn("allocationz", llm.prompts[1])          # the repair prompt carries Lean's error
        entry = self.ledger().get("c1")
        self.assertEqual((entry.status, entry.predicate), ("proposed", GOOD))

    def test_a_predicate_still_broken_after_the_repair_is_kept_but_not_checked(self):
        from companion.extract import extract
        llm = Answers({"ops": [{"op": "add", "text": "broken", "quote": "q", "predicate": BAD},
                               {"op": "add", "text": "fine", "quote": "q", "predicate": GOOD}]},
                      {"ops": [{"op": "revise", "id": "c1", "predicate": BAD}]})
        extract(self.cfg, "a message", llm)
        ledger = self.ledger()
        self.assertEqual(ledger.get("c1").status, "unformalized")
        self.assertEqual(ledger.get("c2").status, "proposed")

    def test_a_message_that_drops_an_earlier_rule_retires_it(self):
        from companion.extract import extract
        extract(self.cfg, "first", Answers({"ops": [{"op": "add", "text": "rule", "quote": "keep it bound",
                                                     "predicate": GOOD}]}))
        extract(self.cfg, "never mind that", Answers({"ops": [{"op": "retire", "id": "c1", "quote": "never mind that",
                                                                "reason": "the developer dropped it"}]}))
        entry = self.ledger().get("c1")
        self.assertEqual(entry.status, "retired")
        self.assertEqual(entry.last_turn, 2)

    def test_a_message_with_no_constraint_changes_nothing(self):
        from companion.extract import extract
        extract(self.cfg, "add a list command please", Answers({"ops": []}))
        self.assertEqual(self.ledger().entries, [])


if __name__ == "__main__":
    unittest.main()
