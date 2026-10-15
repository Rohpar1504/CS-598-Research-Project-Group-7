"""Trace validation (companion.trace_validation.validate).

What we want: it says whether the model and the real program agree on each trace, and
which properties the real program's transitions violate. Doing less work (shared prefixes,
reading state only where the model says it changed, parallel workers) must flag exactly the
traces that checking every step of every trace on its own flags.

The reference for "checking every step of every trace on its own" is `validate_trace`, which
runs one trace from scratch and compares outputs and the whole state after every step.
"""

import tempfile
import unittest
from pathlib import Path

from tests.support import WORKERS, FORGE_MODEL, edits, one_trace_at_a_time, program_copy, requires_forge, trace_set


@requires_forge
class ValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from companion.lean import LeanModel
        cls.model = LeanModel(FORGE_MODEL)
        cls.traces = trace_set(cls.model, seed=7)
        cls.dir = Path(tempfile.mkdtemp())
        cls.programs = {"reference": program_copy(cls.dir / "reference")}
        for bug in ("bind-no-duplicate-guard", "bind-not-atomic", "typea-not-doubled", "tag-duplicate-allowed"):
            cls.programs[bug] = program_copy(cls.dir / bug, edits(bug))
        cls.truth = {name: one_trace_at_a_time(cls.model, p, cls.traces) for name, p in cls.programs.items()}

    def validate(self, name, observe):
        from companion.adapters.forge import ForgeAdapter
        from companion.trace_validation import validate
        program = self.programs[name]
        return validate(lambda: ForgeAdapter(program), self.model, self.traces, workers=WORKERS, observe=observe)

    @staticmethod
    def flagged(results) -> set:
        return {i for i, r in enumerate(results) if r.first_mismatch}

    def test_the_model_matches_the_unchanged_program(self):
        self.assertEqual(self.flagged(self.truth["reference"]), set())
        self.assertEqual(self.flagged(self.validate("reference", "changes")), set())

    def test_every_seeded_bug_is_flagged(self):
        for bug in ("bind-no-duplicate-guard", "bind-not-atomic", "typea-not-doubled", "tag-duplicate-allowed"):
            self.assertTrue(self.flagged(self.truth[bug]), f"{bug}: no trace flagged by checking every step")

    def test_doing_less_work_flags_exactly_the_same_traces(self):
        for name in self.programs:
            for observe in ("all", "changes"):
                with self.subTest(program=name, observe=observe):
                    self.assertEqual(self.flagged(self.validate(name, observe)), self.flagged(self.truth[name]))

    def test_reading_every_step_reports_the_step_where_behavior_first_differs(self):
        results = self.validate("bind-no-duplicate-guard", "all")
        for got, truth in zip(results, self.truth["bind-no-duplicate-guard"]):
            self.assertEqual(got.first_mismatch and got.first_mismatch["step"],
                             truth.first_mismatch and truth.first_mismatch["step"])

    def test_a_write_the_model_did_not_predict_is_caught_even_when_reading_state_less_often(self):
        # bind-not-atomic changes state on a request it rejects; the model predicts no change
        # there, so the state is not read at that step. The end of the trace must catch it.
        self.assertTrue(self.flagged(self.validate("bind-not-atomic", "changes")))

    def test_property_violations_are_those_of_the_real_runs(self):
        results = self.validate("reference", "all")
        for got, truth in zip(results, self.truth["reference"]):
            self.assertEqual(sorted(map(str, got.violations)), sorted(map(str, truth.violations)))
        self.assertTrue(any(r.violations for r in results))   # the [extra] properties do fail on forge

    def test_output_the_model_cannot_express_is_a_mismatch_not_a_crash(self):
        from companion.adapters.forge import ForgeAdapter
        from companion.trace_validation import validate
        program = program_copy(self.dir / "uppercase", [('        "name": name,\n', '        "name": name.upper(),\n')])
        results = validate(lambda: ForgeAdapter(program), self.model, self.traces, workers=WORKERS, observe="changes")
        # Every trace in which the model predicts that a blueprint is created (so the program
        # returns its name, now uppercase) must be flagged.
        expected = self.model.replay_many(self.traces)
        creates = {i for i, e in enumerate(expected)
                   if any(isinstance(s["out"]["body"], dict) and "blueprint" in s["out"]["body"] for s in e["steps"])}
        self.assertTrue(creates)
        self.assertLessEqual(creates, self.flagged(results))


if __name__ == "__main__":
    unittest.main()
