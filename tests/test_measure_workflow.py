"""The measurement workflow's own rules (.github/workflows/measure-more.yml), read as text as test_workflow.py reads
build.yml: it publishes nothing, runs no schedule, gives the token to the one step that writes the report, and makes
today's keyed file with exactly the build's commands; and builder/put_report.sh, run with a stand-in `gh`."""
import base64, os, re, stat, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_workflow import steps  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, '.github', 'workflows', 'measure-more.yml')
BUILD = os.path.join(HERE, '.github', 'workflows', 'build.yml')
PUT = os.path.join(HERE, 'builder', 'put_report.sh')
REPORT_STEP = 'The report, written to this branch'


def commands(chunk):
    return [line.strip() for line in chunk.split('run: |', 1)[1].splitlines() if line.strip().startswith('python3')]


class TestMeasureWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PATH) as f:
            cls.text = f.read()
        with open(BUILD) as f:
            cls.build = f.read()
        cls.steps = steps(cls.text)

    def test_it_publishes_nothing_and_runs_no_schedule(self):
        triggers = self.text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertNotIn('schedule:', triggers)
        self.assertNotIn('cron:', self.text)
        self.assertNotIn('gh release', self.text)
        self.assertIn("  push:\n    branches: [measure-more]\n", self.text)
        self.assertIn('\n  workflow_dispatch:\n', self.text)

    def test_the_report_goes_to_the_branch_that_runs_it(self):
        self.assertEqual(self.steps[REPORT_STEP].count(' measure-more '), 1)

    def test_no_input_or_variable_is_pasted_into_a_script(self):
        for name, chunk in self.steps.items():
            if 'run:' in chunk:
                self.assertNotIn('${{', chunk.split('run:', 1)[1], name)

    def test_the_token_reaches_only_the_report_step(self):
        job_env = self.text.split('\n    steps:\n', 1)[0]
        self.assertNotIn('github.token', job_env)
        self.assertNotIn('secrets.', self.text)
        with_token = [n for n, c in self.steps.items() if 'github.token' in c or 'GH_TOKEN' in c]
        self.assertEqual(with_token, [REPORT_STEP])
        self.assertEqual(list(self.steps)[-1], REPORT_STEP)
        run = self.steps[REPORT_STEP].split('run:', 1)[1].strip()
        self.assertTrue(run.startswith('sh builder/put_report.sh '), run)
        self.assertNotRegex(run, r'python3|perl|\n')
        for name, chunk in self.steps.items():
            if name != REPORT_STEP and 'run:' in chunk:
                self.assertNotRegex(chunk.split('run:', 1)[1], r'(^|\s)gh ', name)

    def test_a_checkout_leaves_no_token_behind_and_every_action_is_a_fixed_commit(self):
        uses = [c for c in self.steps.values() if 'actions/checkout@' in c]
        self.assertEqual(len(uses), 2)
        for c in uses:
            self.assertIn('persist-credentials: false', c)
        for ref in re.findall(r'uses: \S+@(\S+)', self.text):
            self.assertRegex(ref, r'^[0-9a-f]{40}$')
        m = re.search(r"DSC_REF: \$\{\{ vars\.DSC_REF \|\| '([^']*)' \}\}", self.text)
        self.assertEqual(m.group(1), re.search(r"DSC_REF: \$\{\{ vars\.DSC_REF \|\| '([^']*)' \}\}", self.build).group(1))
        first = list(self.steps.values())[0]
        self.assertIn('"${#DSC_REF}" -eq 40', first)
        self.assertLess(self.text.index('"${#DSC_REF}" -eq 40'), self.text.index('actions/checkout@'))

    def test_todays_file_is_made_with_the_builds_own_commands(self):
        ours = commands(self.steps["Today's keyed file (the build's steps)"])
        theirs = commands(steps(self.build)['Fetch, the tables, the records, the keyed file'])
        self.assertEqual(ours, theirs)

    def test_the_first_steps_checks_refuse_what_they_should(self):
        script = self.steps['The inputs, checked before anything runs'].split('run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        good = {'DSC_REF': 'a' * 40, 'DUMP': '20261114-002230', 'BITS': '14'}

        def run(**change):
            env = {**good, **change, 'PATH': os.environ['PATH']}
            return subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True).returncode

        self.assertEqual(run(), 0)
        self.assertEqual(run(DUMP=''), 0)
        for change in ({'DSC_REF': 'dev'}, {'DSC_REF': 'a' * 39}, {'DUMP': '2026"; id; "'}, {'BITS': '14; id'},
                       {'BITS': ''}):
            with self.subTest(change=change):
                self.assertEqual(run(**change), 1)


class TestPutReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, 'gh.log')
        gh = os.path.join(self.tmp, 'gh')
        with open(gh, 'w') as f:
            f.write('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$GH_LOG"\necho abc123\n')
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.report = os.path.join(self.tmp, 'r.txt')
        with open(self.report, 'w', encoding='utf-8') as f:
            f.write('More record kinds: Björk\n' * 50)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def run_put(self, *args):
        env = {'PATH': self.tmp + os.pathsep + os.environ['PATH'], 'GH_LOG': self.log}
        return subprocess.run(['sh', PUT, *args], env=env, capture_output=True, text=True)

    def test_it_writes_the_file_to_the_branch(self):
        r = self.run_put('o/r', 'measure-more', 'reports/more-1-run2.txt', self.report)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.log) as f:
            a = f.read().splitlines()
        self.assertEqual(a[:3], ['api', '-X', 'PUT'])
        self.assertEqual(a[3], 'repos/o/r/contents/reports/more-1-run2.txt')
        fields = dict(x.split('=', 1) for x in a[4:] if '=' in x)
        self.assertEqual(fields['branch'], 'measure-more')
        self.assertEqual(fields['message'], 'more-1-run2.txt')
        with open(self.report, 'rb') as f:
            self.assertEqual(base64.b64decode(fields['content']), f.read())

    def test_it_refuses_what_it_should(self):
        empty = os.path.join(self.tmp, 'empty.txt')
        open(empty, 'w').close()
        for args in (('or', 'measure-more', 'reports/a.txt', self.report),
                     ('o/r', 'main; id', 'reports/a.txt', self.report),
                     ('o/r', '', 'reports/a.txt', self.report),
                     ('o/r', 'measure-more', 'builder/a.txt', self.report),
                     ('o/r', 'measure-more', 'a.txt', self.report),
                     ('o/r', 'measure-more', 'reports/a.md', self.report),
                     ('o/r', 'measure-more', 'reports/x/a.txt', self.report),
                     ('o/r', 'measure-more', 'reports/a b.txt', self.report),
                     ('o/r', 'measure-more', 'reports/a.txt', empty),
                     ('o/r', 'measure-more', 'reports/a.txt', os.path.join(self.tmp, 'none.txt'))):
            with self.subTest(args=args):
                r = self.run_put(*args)
                self.assertEqual(r.returncode, 1, args)
                self.assertFalse(os.path.exists(self.log), args)


if __name__ == '__main__':
    unittest.main()
