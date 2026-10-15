"""Running the program under test (companion.runner).

What we want: running the program through the runner behaves exactly like starting it as a
new process, only faster, and it can record which lines of the program each run executed.
"""

import os
import re
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from companion import runner
from tests.support import REFERENCE, requires_forge


def normalize(text: str) -> str:
    """Forge output without its random UUIDs and timestamps."""
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", text)
    return re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<time>", text)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name: str, source: str) -> Path:
        path = self.dir / name
        path.write_text(textwrap.dedent(source))
        return path

    def in_new_process(self, program, args, stdin=None):
        old = os.environ.get("COMPANION_RUNNER")
        os.environ["COMPANION_RUNNER"] = "subprocess"
        try:
            return runner.run(program, args, stdin)
        finally:
            if old is None:
                del os.environ["COMPANION_RUNNER"]
            else:
                os.environ["COMPANION_RUNNER"] = old

    @requires_forge
    def test_forge_behaves_the_same_as_in_a_new_process(self):
        steps = [
            (["schema", "init"], None),
            (["schema", "init"], None),                                    # conflict the second time
            (["blueprint", "create"], '{"name": "bp-0", "requirement_sets": [{"resource_type": "r", "resource_count": 1}]}'),
            (["blueprint", "create"], '{"name": ""}'),                       # validation error
            (["--revision", "1.2", "blueprint", "get", "bp-0"], None),
            (["--revision", "1.x", "blueprint", "list"], None),              # parse error
            (["blueprint", "get", "00000000-0000-4000-8000-000000000000"], None),
            (["allocation", "create"], '{"blueprint_name": "bp-0"}'),
            (["allocation", "list"], None),
            (["no-such-command", "x"], None),
        ]
        fast, slow = self.dir / "fast", self.dir / "slow"
        for args, stdin in steps:
            a = runner.run(REFERENCE, ["--data-dir", fast, *args], stdin)
            b = self.in_new_process(REFERENCE, ["--data-dir", slow, *args], stdin)
            self.assertEqual((a.code, normalize(a.stdout)), (b.code, normalize(b.stdout)), args)

    def test_exit_codes_and_failures_are_those_of_a_new_process(self):
        program = self.write("p.py", """
            import sys
            def main(argv):
                if argv[0] == "three": return 3
                if argv[0] == "raise": raise RuntimeError("boom")
                if argv[0] == "exit-message": sys.exit("bad")
                if argv[0] == "exit-none": sys.exit()
                print("ok"); return 0
            if __name__ == "__main__":
                sys.exit(main(sys.argv[1:]))
            """)
        for arg in ("three", "raise", "exit-message", "exit-none", "fine"):
            a, b = runner.run(program, [arg]), self.in_new_process(program, [arg])
            self.assertEqual((a.code, a.stdout), (b.code, b.stdout), arg)
            if arg == "raise":
                self.assertIn("RuntimeError: boom", a.stderr)

    def test_stdin_and_arguments_reach_the_program(self):
        program = self.write("echo.py", """
            import sys
            def main(argv):
                print(argv, sys.stdin.read()); return 0
            """)
        self.assertEqual(runner.run(program, ["a", "b"], "input").stdout, "['a', 'b'] input\n")

    def test_a_program_that_hangs_times_out(self):
        program = self.write("hang.py", "def main(argv):\n    while True: pass\n")
        start = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            runner.run(program, [], timeout=1)
        self.assertLess(time.time() - start, 10)

    def test_edits_to_the_program_take_effect(self):
        program = self.write("v.py", "def main(argv):\n    print('one'); return 0\n")
        self.assertEqual(runner.run(program, []).stdout, "one\n")
        program.write_text("def main(argv):\n    print('two'); return 0\n")
        os.utime(program, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        self.assertEqual(runner.run(program, []).stdout, "two\n")

    def test_programs_that_cannot_be_forked_still_run_correctly(self):
        marker = self.dir / "ran"
        script = self.write("script.py", f"""
            import sys
            with open({str(marker)!r}, "a") as f: f.write("x")
            print('top level', sys.argv[1:])
            """)                                                                          # no main()
        broken = self.write("broken.py", "def main(argv)\n    return 0\n")                  # syntax error
        with runner.recording() as rec:
            self.assertEqual(runner.run(script, ["x"]).stdout, "top level ['x']\n")
        self.assertFalse(rec.complete)       # no lines recorded for it, and that is reported
        self.assertEqual(marker.read_text(), "x")    # it ran once, as its own process, not also in ours
        result = runner.run(broken, [])
        self.assertNotEqual(result.code, 0)
        self.assertIn("SyntaxError", result.stderr)

    def test_records_the_lines_each_run_executes(self):
        program = self.write("branches.py", """
            def main(argv):
                if argv[0] == "left":
                    print("L")
                else:
                    print("R")
                return 0
            """)
        with runner.recording() as left:
            runner.run(program, ["left"])
        with runner.recording() as right:
            runner.run(program, ["right"])
        self.assertTrue(left.complete and right.complete)
        lines_l, lines_r = left.lines[str(program)], right.lines[str(program)]
        self.assertIn(4, lines_l)
        self.assertNotIn(6, lines_l)
        self.assertIn(6, lines_r)
        self.assertNotIn(4, lines_r)


if __name__ == "__main__":
    unittest.main()
