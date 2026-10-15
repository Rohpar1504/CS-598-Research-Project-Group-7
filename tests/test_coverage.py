"""Changed code that no validation trace reaches (companion.coverage).

What we want: after an edit, every new or changed statement inside the program's functions
that no trace executed is reported, since validation says nothing about it; a statement
that some trace executed is not, even if Python recorded only one of its lines; changes that
are not code a trace could run (comments, blank lines, docstrings, module-level code) are
not reported; and if coverage is unknown, the answer is "unknown", not "nothing".
"""

import textwrap
import unittest

from companion.coverage import uncovered_changes

OLD = textwrap.dedent('''\
    LIMIT = 3


    def bind(payload):
        """Bind allocations."""
        if not payload:
            return "error"
        return "ok"
    ''')


def gaps(new: str, ran: set, old: str = OLD):
    return uncovered_changes({"p.py": old}, {"p.py": textwrap.dedent(new)}, {"p.py": ran})


class CoverageTest(unittest.TestCase):
    def test_a_new_branch_no_trace_ran_is_reported(self):
        new = '''\
            LIMIT = 3


            def bind(payload):
                """Bind allocations."""
                if not payload:
                    return "error"
                if payload == "reserved":
                    return "forbidden"
                return "ok"
            '''
        # Traces ran the new condition (line 8) but never took the new branch.
        self.assertEqual([(g["line"], g["text"]) for g in gaps(new, {6, 8, 10})], [(9, 'return "forbidden"')])
        # Traces that all stopped at the first check ran none of the new code.
        self.assertEqual([g["line"] for g in gaps(new, {6, 7})], [8, 9])

    def test_a_changed_statement_that_ran_is_not_reported(self):
        new = '''\
            LIMIT = 3


            def bind(payload):
                """Bind allocations."""
                if not payload:
                    return "error"
                return ("ok"
                        + "!")
            '''
        self.assertEqual(gaps(new, {6, 8}), [])               # line 9 is not recorded, but the statement ran

    def test_changes_no_trace_could_run_are_not_reported(self):
        new = '''\
            LIMIT = 4
            # a comment


            def bind(payload):
                """Bind the allocations."""

                if not payload:
                    return "error"
                return "ok"
            '''
        self.assertEqual(gaps(new, set()), [])

    def test_an_unchanged_program_has_nothing_to_report(self):
        self.assertEqual(gaps(OLD, set()), [])

    def test_unknown_coverage_is_not_reported_as_full_coverage(self):
        self.assertIsNone(uncovered_changes({"p.py": OLD}, {"p.py": OLD + "\n"}, None))


if __name__ == "__main__":
    unittest.main()
