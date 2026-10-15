"""Writing the model and keeping it in step with the code (companion.sync, through `check`).

What we want: an LLM agent writes the model and adapter for each application, and the model
keeps doing what the code does as the code changes.
- After synthesis, the model matches the program, and constraints are checked against it.
- When nothing changed in the code, the agent is not called.
- When an edit makes the model disagree with the code, the agent repairs the model, and the
  constraints are then checked against the repaired model: a bug the edit introduced is
  found, and reproduced on the real program.
- When the repair does not succeed, nothing is claimed checked, and a repair that leaves a
  model that does not build is undone.
- When an edit adds code that no validation trace reaches (new behavior the model may not
  cover), the agent is asked about it, once.
- Constraints the model could not express are tried again once the model changes.
- When the search never reaches the situation a rule is about, the agent is asked, once, to
  widen the search, and the rule is then really checked.

The agent is a stand-in that edits the workspace's files as an LLM would. The model it
starts from (or "writes") is the hand-built forge model of lean/Forge.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import (EXAMPLE_LEDGER, TAG_UPDATE_REJECTED, StandInAgent, apply, edit_file, edits,
                           forge_workspace, ledger_file, program_copy, requires_forge, seed_forge)

KEPT = [f"c{i}" for i in range(1, 9)]          # rules forge's reference solution keeps
BROKEN = ["c9", "c10"]                          # the two "extra" rules it breaks

# The model's bind command, and the same command after the edit `bind-no-duplicate-guard`
# (which drops the program's refusal to bind an assignment that is already bound).
DUPLICATE_GUARD = """    else if ops.any (·.2 == .add) && s.allocations.any (fun al => al.bound && al.assignment == some a)
      then (s, .error "duplicate_assignment")
"""

# A new branch in the program that no abstract value of the model leads to.
RESERVED = ("""        return _emit_error("validation_error", "assignment_id")

    operations = payload.get("allocations")""",
            """        return _emit_error("validation_error", "assignment_id")
    if assignment_id.startswith("reserved-"):
        return _emit_error("forbidden", assignment_id)

    operations = payload.get("allocations")""")


def follow_the_dropped_guard(ws: Path) -> None:
    """The repair an LLM should make after `bind-no-duplicate-guard`."""
    edit_file(ws / "lean" / "Forge" / "Model.lean", DUPLICATE_GUARD, "")


def break_the_model(ws: Path) -> None:
    (ws / "lean" / "Forge" / "Model.lean").write_text("this is not Lean")


def do_nothing(ws: Path) -> None:
    pass


@requires_forge
class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.program = program_copy(self.dir / "program")

    def check(self, cfg, agent=None, llm=None):
        from companion.check import check
        return check(cfg, log=lambda m: None, agent=agent, llm=llm)

    def evidence(self, report):
        return {e["id"]: e["evidence"] for e in report["entries"]}

    def synced_to_current_code(self, cfg) -> bool:
        from companion.sync import baseline, current_sources
        return baseline(cfg) == current_sources(cfg)

    def assert_reproduction_breaks(self, cfg, entry):
        """Run the reported trace again on the real program: the constraint must fail."""
        from companion.lean import LeanModel
        from companion.trace_validation import validate_trace
        adapter = cfg.make_adapter()
        try:
            result = validate_trace(adapter, LeanModel(cfg.exe_path), entry["trace"])
        finally:
            adapter.close()
        self.assertIn(entry["id"], {n.split(":")[0] for _, names in result.violations for n in names})

    def test_synthesis_writes_a_model_that_matches_and_constraints_are_checked_against_it(self):
        from companion.sync import synthesize
        cfg = forge_workspace(self.dir / "ws", self.program, ledger=EXAMPLE_LEDGER, seeded=False)
        agent = StandInAgent(lambda ws: seed_forge(cfg))
        result = synthesize(cfg, agent, log=lambda m: None)
        self.assertTrue(result["matches"])
        self.assertEqual(len(agent.calls), 1)
        call = agent.calls[0]
        self.assertEqual(call["cwd"], cfg.workspace)
        self.assertIn(self.program.parent, call["add_dirs"])            # it may read the program's code
        self.assertIn(str(self.program), call["task"])
        self.assertTrue(self.synced_to_current_code(cfg))

        report = self.check(cfg, StandInAgent())
        evidence = self.evidence(report)
        self.assertEqual([evidence[c] for c in KEPT], ["checked"] * len(KEPT))
        self.assertEqual([evidence[c] for c in BROKEN], ["violated"] * len(BROKEN))
        self.assertEqual(report["repairs"], [])                       # nothing changed: no agent call

    def test_after_an_edit_breaks_the_model_the_agent_repairs_it_and_the_new_bug_is_found(self):
        cfg = forge_workspace(self.dir / "ws", self.program, ledger=EXAMPLE_LEDGER, transition_depth=3)
        self.check(cfg)                                               # the model matches this code
        apply(self.program, edits("bind-no-duplicate-guard"))          # the coding agent's edit
        agent = StandInAgent(follow_the_dropped_guard)
        report = self.check(cfg, agent)

        self.assertEqual(len(agent.calls), 1)
        task = agent.calls[0]["task"]
        self.assertIn('-                return _emit_error("duplicate_assignment", assignment_id)', task)   # the diff
        self.assertIn("Where the model and the program disagree", task)
        self.assertTrue(report["model_repaired"] and report["model_matches_code"])
        evidence = {e["id"]: e for e in report["entries"]}
        self.assertEqual(evidence["c3"]["evidence"], "violated")      # the rule this edit breaks
        self.assert_reproduction_breaks(cfg, evidence["c3"])
        for c in ("c1", "c2", "c4", "c5", "c6", "c7", "c8"):
            self.assertEqual(evidence[c]["evidence"], "checked", c)
        self.assertTrue(self.synced_to_current_code(cfg))

    def test_when_the_repair_does_not_succeed_nothing_is_claimed_checked(self):
        cfg = forge_workspace(self.dir / "ws", self.program, ledger=EXAMPLE_LEDGER, transition_depth=3)
        self.check(cfg)
        model_before = (cfg.lean_dir / "Forge" / "Model.lean").read_text()
        apply(self.program, edits("bind-no-duplicate-guard"))
        agent = StandInAgent(break_the_model, do_nothing)
        report = self.check(cfg, agent)

        self.assertEqual(len(agent.calls), 2)                         # the configured number of rounds
        self.assertFalse(report["model_matches_code"])
        self.assertFalse(report["repairs"][0]["fixed"])
        self.assertNotIn("checked", self.evidence(report).values())
        self.assertEqual(self.evidence(report)["c3"], "violated")     # seen on the real program all the same
        self.assertEqual((cfg.lean_dir / "Forge" / "Model.lean").read_text(), model_before)   # undone
        self.assertFalse(self.synced_to_current_code(cfg))

    def test_new_code_that_no_trace_reaches_is_sent_to_the_agent_once(self):
        cfg = forge_workspace(self.dir / "ws", self.program, ledger=EXAMPLE_LEDGER)
        self.check(cfg)
        apply(self.program, [RESERVED])
        agent = StandInAgent(do_nothing)
        report = self.check(cfg, agent)

        self.assertTrue(report["model_matches_code"])                 # validation alone cannot see it
        self.assertEqual(len(agent.calls), 1)
        self.assertIn('if assignment_id.startswith("reserved-"):', agent.calls[0]["task"])
        self.assertIn("forge.py", {g["file"] for g in report["unreached_changes"]})
        self.assertEqual(self.check(cfg, agent)["repairs"], [])       # not asked again
        self.assertEqual(len(agent.calls), 1)

    def test_constraints_the_model_could_not_express_are_tried_again_after_it_changes(self):
        ledger = json.loads(EXAMPLE_LEDGER.read_text())
        ledger["entries"] = [e for e in ledger["entries"] if e["id"] in ("c1", "c3")]
        ledger["entries"].append({"id": "c11", "text": "at most 100 allocations",
                                  "predicate": "fun t => t.after.allocations.length ≤ maxAllocations",
                                  "kind": "state", "status": "proposed", "confidence": "high",
                                  "provenance": [{"turn": 1, "quote": "q"}], "history": [], "error": None})
        path = self.dir / "ledger.json"
        path.write_text(json.dumps(ledger))
        cfg = forge_workspace(self.dir / "ws", self.program, ledger=path, transition_depth=3)
        self.assertEqual(self.evidence(self.check(cfg))["c11"], "unchecked")   # no `maxAllocations` yet

        def repair_and_add_the_name(ws):
            follow_the_dropped_guard(ws)
            edit_file(ws / "lean" / "Forge" / "Props.lean", "end Forge", "def maxAllocations : Nat := 100\n\nend Forge")
        apply(self.program, edits("bind-no-duplicate-guard"))
        report = self.check(cfg, StandInAgent(repair_and_add_the_name))
        self.assertEqual(self.evidence(report)["c11"], "checked")

    def test_when_search_never_reaches_a_rules_situation_the_agent_widens_it_once(self):
        def add_tag_update_to_an_area(ws):
            edit_file(ws / "lean" / "Forge" / "Model.lean", "act (.tagDelete 0), act (.modList .all)],",
                      "act (.tagDelete 0), act .tagUpdate, act (.modList .all)],")
        cfg = forge_workspace(self.dir / "ws", self.program, ledger=ledger_file(self.dir, TAG_UPDATE_REJECTED))
        agent = StandInAgent(add_tag_update_to_an_area)
        report = self.check(cfg, agent)

        self.assertEqual(len(agent.calls), 1)
        self.assertIn("Rules whose situation the search never reaches", agent.calls[0]["task"])
        self.assertIn("A tag update is requested.", agent.calls[0]["task"])
        self.assertEqual(self.evidence(report)["c1"], "checked")
        self.assertIn("situation is reached", report["entries"][0]["detail"])
        self.check(cfg, agent)
        self.assertEqual(len(agent.calls), 1)                          # not asked again


if __name__ == "__main__":
    unittest.main()
