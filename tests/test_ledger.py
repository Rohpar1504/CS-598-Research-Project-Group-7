"""The constraint ledger (companion.ledger).

What we want: every active constraint whose Lean predicate typechecks becomes a property of
the app's executable, named by its id; a constraint whose predicate does not typecheck is
kept, marked unformalized with Lean's error, and does not stop the others from being checked;
retired and superseded constraints are no longer checked, and revisions keep their history;
a constraint's situation becomes a probe property, and a situation that does not typecheck
sets the constraint aside like a predicate that does not.
"""

import tempfile
import unittest
from pathlib import Path

from tests.support import quick_config, requires_forge

GOOD = "fun t => t.after.allocations.all fun a => (a.status == \"bound\") == a.assignment.isSome"
ALSO_GOOD = "fun t =>\n  let keys := t.after.tags.map fun g => (g.module, g.key)\n  keys.eraseDups.length == keys.length"
BAD = "fun t => t.after.tagz.length == 0"


@requires_forge
class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = quick_config(Path(tempfile.mkdtemp()))

    def ledger(self, *ops):
        from companion.ledger import Ledger
        ledger = Ledger.load(self.cfg.ledger_path)
        ledger.apply(list(ops), turn=1)
        return ledger

    def build(self, ledger) -> list:
        """Build the executable from the ledger; returns its property names."""
        from companion.check import build
        from companion.lean import LeanModel
        build(self.cfg, ledger)
        ledger.save()
        return LeanModel(self.cfg.exe_path).properties()

    def test_constraints_that_typecheck_become_properties_named_by_id(self):
        ledger = self.ledger({"op": "add", "text": "bound iff assigned", "predicate": GOOD},
                             {"op": "add", "text": "tag keys unique", "predicate": ALSO_GOOD})
        self.assertEqual(self.build(ledger), ["c1: bound iff assigned", "c2: tag keys unique"])

    def test_a_constraint_that_does_not_typecheck_is_set_aside_and_the_rest_still_build(self):
        ledger = self.ledger({"op": "add", "text": "bound iff assigned", "predicate": GOOD},
                             {"op": "add", "text": "misspelled", "predicate": BAD},
                             {"op": "add", "text": "tag keys unique", "predicate": ALSO_GOOD})
        names = self.build(ledger)
        self.assertEqual(names, ["c1: bound iff assigned", "c3: tag keys unique"])
        bad = ledger.get("c2")
        self.assertEqual(bad.status, "unformalized")
        self.assertIn("tagz", bad.error)

    def test_a_situation_becomes_a_probe_and_one_that_does_not_typecheck_sets_the_rule_aside(self):
        ledger = self.ledger({"op": "add", "text": "bound iff assigned", "predicate": GOOD,
                              "situation": "a bind", "situation_predicate": "fun t => t.action.cmd.isBlueprintWrite"},
                             {"op": "add", "text": "tag keys unique", "predicate": ALSO_GOOD,
                              "situation": "misspelled", "situation_predicate": "fun t => t.before.tagz.isEmpty"})
        self.assertEqual(self.build(ledger), ["c1: bound iff assigned", "c1?reach"])
        self.assertEqual(ledger.get("c2").status, "unformalized")
        self.assertIn("tagz", ledger.get("c2").error)

    def test_retired_and_superseded_constraints_are_no_longer_checked(self):
        ledger = self.ledger({"op": "add", "text": "one", "predicate": GOOD},
                             {"op": "add", "text": "two", "predicate": ALSO_GOOD},
                             {"op": "add", "text": "three", "predicate": GOOD})
        ledger.apply([{"op": "retire", "id": "c1", "reason": "the developer dropped it"},
                      {"op": "supersede", "id": "c3", "reason": "replaced"}], turn=2)
        self.assertEqual(self.build(ledger), ["c2: two"])
        self.assertEqual(ledger.get("c1").status, "retired")

    def test_a_revision_replaces_the_predicate_and_keeps_the_history(self):
        ledger = self.ledger({"op": "add", "text": "first wording", "predicate": BAD, "quote": "q1"})
        self.build(ledger)                                   # does not typecheck: unformalized
        ledger.apply([{"op": "revise", "id": "c1", "text": "second wording", "predicate": GOOD, "quote": "q2"}], turn=2)
        self.assertEqual(self.build(ledger), ["c1: second wording"])
        entry = ledger.get("c1")
        self.assertEqual(entry.status, "proposed")
        self.assertEqual([h["text"] for h in entry.history], ["first wording"])
        self.assertEqual([p["quote"] for p in entry.provenance], ["q1", "q2"])
        self.assertEqual(entry.last_turn, 2)


if __name__ == "__main__":
    unittest.main()
