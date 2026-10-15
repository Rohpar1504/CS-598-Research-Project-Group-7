"""Checking the code after a turn (companion.check).

What we want: a constraint is reported checked only if the model matches the code and no
violation was found; a constraint the code breaks is reported violated with a reproduction
that really breaks it on the real program; when the model no longer matches the code,
nothing is claimed checked; a constraint that does not typecheck is reported unchecked; a
constraint is claimed checked only if the search reaches the situation it is about.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import (EXAMPLE_LEDGER, REFERENCE, TAG_KEYS_UNIQUE, TAG_UPDATE_REJECTED, edits, ledger_file,
                           program_copy, quick_config, requires_forge)

KEPT = [f"c{i}" for i in range(1, 9)]          # rules forge's reference solution keeps
BROKEN = ["c9", "c10"]                          # the two "extra" rules it breaks


@requires_forge
class CheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def check(self, cfg):
        from companion.check import check
        return check(cfg, log=lambda m: None)

    def assert_reproduction_breaks(self, cfg, entry):
        """Run the reported trace again on the real program: the constraint must fail."""
        from companion.adapters.forge import ForgeAdapter
        from companion.lean import LeanModel
        from companion.trace_validation import validate_trace
        adapter = ForgeAdapter(cfg.program)
        try:
            result = validate_trace(adapter, LeanModel(cfg.exe_path), entry["trace"])
        finally:
            adapter.close()
        violated = {name.split(":")[0] for _, names in result.violations for name in names}
        self.assertIn(entry["id"], violated)
        self.assertEqual(len(entry["reproduction"]), len(entry["trace"]))

    def test_kept_rules_are_checked_and_broken_ones_come_with_working_reproductions(self):
        cfg = quick_config(self.dir, ledger=EXAMPLE_LEDGER)
        report = self.check(cfg)
        evidence = {e["id"]: e for e in report["entries"]}
        self.assertTrue(report["model_matches_code"])
        for c in KEPT:
            self.assertEqual(evidence[c]["evidence"], "checked", c)
        for c in BROKEN:
            self.assertEqual(evidence[c]["evidence"], "violated", c)
            self.assert_reproduction_breaks(cfg, evidence[c])

    def test_nothing_is_claimed_checked_when_the_model_no_longer_matches_the_code(self):
        program = program_copy(self.dir / "program", edits("bind-no-duplicate-guard"))
        cfg = quick_config(self.dir, program=program, ledger=EXAMPLE_LEDGER, transition_depth=3)
        report = self.check(cfg)
        self.assertFalse(report["model_matches_code"])
        self.assertNotIn("checked", {e["evidence"] for e in report["entries"]})
        c3 = next(e for e in report["entries"] if e["id"] == "c3")   # the rule this edit breaks
        self.assertEqual(c3["evidence"], "violated")
        self.assert_reproduction_breaks(cfg, c3)

    def test_a_rule_that_does_not_typecheck_is_reported_unchecked(self):
        ledger = json.loads(EXAMPLE_LEDGER.read_text())
        ledger["entries"].append({"id": "c11", "text": "misspelled", "predicate": "fun t => t.after.tagz.isEmpty",
                                  "kind": "state", "status": "confirmed", "confidence": "high",
                                  "provenance": [{"turn": 8, "quote": "q"}], "history": [], "error": None})
        path = self.dir / "ledger.json"
        path.write_text(json.dumps(ledger))
        cfg = quick_config(self.dir, ledger=path)
        evidence = {e["id"]: e for e in self.check(cfg)["entries"]}
        self.assertEqual(evidence["c11"]["evidence"], "unchecked")
        self.assertIn("tagz", evidence["c11"]["error"])
        self.assertEqual(evidence["c1"]["evidence"], "checked")

    def test_a_rule_is_claimed_checked_only_if_the_search_reaches_its_situation(self):
        cfg = quick_config(self.dir, ledger=ledger_file(self.dir, TAG_KEYS_UNIQUE, TAG_UPDATE_REJECTED))
        evidence = {e["id"]: e for e in self.check(cfg)["entries"]}
        self.assertEqual(evidence["c1"]["evidence"], "checked")
        self.assertIn("situation is reached", evidence["c1"]["detail"])
        self.assertEqual(evidence["c2"]["evidence"], "unchecked")       # it does hold, but search never tried
        self.assertIn("never reaches the situation", evidence["c2"]["why"])

    def test_checking_again_with_no_change_reruns_nothing_and_agrees(self):
        cfg = quick_config(self.dir, ledger=EXAMPLE_LEDGER)
        first, second = self.check(cfg), self.check(cfg)
        self.assertEqual(second["validation"]["reused"], second["validation"]["traces"])
        self.assertEqual(second["validation"]["steps_run"], 0)
        self.assertEqual([(e["id"], e["evidence"]) for e in first["entries"]],
                         [(e["id"], e["evidence"]) for e in second["entries"]])


if __name__ == "__main__":
    unittest.main()
