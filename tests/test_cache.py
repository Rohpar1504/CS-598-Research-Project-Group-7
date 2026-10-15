"""Reusing validation results across turns (companion.validation_cache).

What we want: after the agent edits the program, validating with the cache gives exactly the
results of validating from scratch, while running only what the edit could have affected.
Results may be reused only when no change can affect the trace.
"""

import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from tests.support import WORKERS, FORGE_MODEL, ROOT, apply, edits, program_copy, requires_forge, trace_set


@requires_forge
class CachedValidationTest(unittest.TestCase):
    """Real edits to forge, checked against validation from scratch."""

    @classmethod
    def setUpClass(cls):
        from companion.lean import LeanModel
        cls.model = LeanModel(FORGE_MODEL)
        cls.traces = trace_set(cls.model, seed=11)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.program = program_copy(self.dir / "program")
        self.cache_path = self.dir / "cache.json"

    def validate(self, cached: bool):
        from companion.adapters.forge import ForgeAdapter
        from companion.trace_validation import Stats, validate
        from companion.validation_cache import ValidationCache
        cache = None
        if cached:
            cache = ValidationCache(self.cache_path, [self.program, self.program.with_suffix(".py")],
                                    ROOT / "companion" / "adapters" / "forge.py")
        stats = Stats()
        program = self.program
        results = validate(lambda: ForgeAdapter(program), self.model, self.traces, workers=WORKERS,
                           observe="changes", cache=cache, stats=stats)
        if cache:
            cache.save()
        return results, stats

    @staticmethod
    def summary(results):
        return [(r.first_mismatch is not None, sorted(map(str, r.violations))) for r in results]

    def edit(self, changes):
        apply(self.program, changes)
        later = time.time_ns() + 10**9
        os.utime(self.program.with_suffix(".py"), ns=(later, later))

    def test_an_unchanged_program_is_not_run_again(self):
        first, _ = self.validate(cached=True)
        again, stats = self.validate(cached=True)
        self.assertEqual(stats.reused, len(self.traces))
        self.assertEqual(stats.steps_run, 0)
        self.assertEqual(self.summary(again), self.summary(first))

    def test_after_an_edit_the_results_are_those_of_a_run_from_scratch(self):
        changes = {
            "bind-no-duplicate-guard": edits("bind-no-duplicate-guard"),
            "typea-not-doubled": edits("typea-not-doubled"),
            "tag-duplicate-allowed": edits("tag-duplicate-allowed"),
            "module-level constant": [("LATEST_SCHEMA_VERSION = 1\n", "LATEST_SCHEMA_VERSION = 2\n")],
        }
        for name, change in changes.items():
            with self.subTest(edit=name):
                self.setUp()
                self.validate(cached=True)                 # turn 1: fills the cache
                self.edit(change)
                cached, stats = self.validate(cached=True)  # turn 2: after the edit
                fresh, _ = self.validate(cached=False)
                self.assertEqual(self.summary(cached), self.summary(fresh))
                self.assertTrue(any(r.first_mismatch for r in fresh), "the edit should change behavior")
                if name != "module-level constant":         # a local edit should not rerun everything
                    self.assertGreater(stats.reused, 0)


class InvalidationRulesTest(unittest.TestCase):
    """Which edits force a trace to run again, on a small program whose behavior we know."""

    PROGRAM = textwrap.dedent('''\
        LIMIT = 10

        def classify(x):
            if x == "a":
                return "A"
            else:
                return "other"

        def unused():
            return 1

        def main(argv):
            print(classify(argv[0]))
            return 0
        ''')

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.program = self.dir / "prog.py"
        self.program.write_text(self.PROGRAM)
        self.adapter_file = self.dir / "adapter.py"
        self.adapter_file.write_text("# adapter v1\n")
        self.path = self.dir / "cache.json"

    def record(self, arg: str):
        """Run the program on `arg` and cache that run as one trace's record."""
        from companion import runner
        from companion.trace_validation import Record
        from companion.validation_cache import ValidationCache
        with runner.recording() as rec:
            out = runner.run(self.program, [arg]).stdout
        cache = ValidationCache(self.path, [self.program], self.adapter_file)
        cache.put(arg, Record([out], [None], None, {f: sorted(l) for f, l in rec.lines.items()}, "changes"))
        cache.save()
        return out

    def reusable_after(self, new_source: str, arg: str = "c") -> bool:
        from companion import runner
        from companion.validation_cache import ValidationCache
        before = self.record(arg)
        self.program.write_text(new_source)
        later = time.time_ns() + 10**9
        os.utime(self.program, ns=(later, later))
        reusable = ValidationCache(self.path, [self.program], self.adapter_file).get(arg, "changes") is not None
        changed = runner.run(self.program, [arg]).stdout != before
        if changed:   # never reuse a result the edit actually changed
            self.assertFalse(reusable, "a result the edit changed was reused")
        return reusable

    def test_a_new_branch_on_the_path_the_trace_takes_reruns_it(self):
        # The trace for "c" runs `if x == "a"` and the `else` branch. A new `elif` sits between
        # two lines it never executed, yet it is on its path and changes its result.
        new = self.PROGRAM.replace('        return "A"\n', '        return "A"\n    elif x == "c":\n        return "C"\n')
        self.assertFalse(self.reusable_after(new))

    def test_an_edit_to_code_the_trace_never_ran_keeps_its_result(self):
        self.assertTrue(self.reusable_after(self.PROGRAM.replace("return 1", "return 2")))

    def test_adding_a_new_function_keeps_results(self):
        self.assertTrue(self.reusable_after(self.PROGRAM + "\ndef helper():\n    return 3\n"))

    def test_a_change_at_module_level_reruns_everything(self):
        self.assertFalse(self.reusable_after(self.PROGRAM.replace("LIMIT = 10", "LIMIT = 11")))

    def test_a_changed_adapter_reruns_everything(self):
        from companion.validation_cache import ValidationCache
        self.record("c")
        self.adapter_file.write_text("# adapter v2\n")
        self.assertIsNone(ValidationCache(self.path, [self.program], self.adapter_file).get("c", "changes"))

    def test_a_reused_record_points_at_the_same_code_after_lines_move(self):
        from companion.validation_cache import ValidationCache
        self.record("c")
        self.program.write_text("# a new comment line at the top\n" + self.PROGRAM)
        record = ValidationCache(self.path, [self.program], self.adapter_file).get("c", "changes")
        if record is not None:   # a comment at module level may conservatively rerun; if reused, lines must move
            lines = record.footprint[str(self.program.resolve())]
            text = self.program.read_text().splitlines()
            self.assertTrue(all(text[l - 1].strip() for l in lines))
            self.assertIn('print(classify(argv[0]))', "\n".join(text[l - 1] for l in lines))


if __name__ == "__main__":
    unittest.main()
