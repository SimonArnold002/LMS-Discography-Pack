"""The build's test run (.github/workflows/build-test.yml), read as text as test_workflow.py reads build.yml: it
publishes nothing, runs no schedule, gives the token to the one step that writes the report, and runs the build's own
commands in the build's order; builder/put_report.sh, run with a stand-in `gh`; and builder/build_report.py on the
made-up dump's sized file and its parts."""
import base64, contextlib, io, json, os, re, shutil, stat, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'builder'))
import build_report, keyed, load_parts  # noqa: E402
from test_more import More  # noqa: E402
from test_workflow import steps  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, '.github', 'workflows', 'build-test.yml')
BUILD = os.path.join(HERE, '.github', 'workflows', 'build.yml')
PUT = os.path.join(HERE, 'builder', 'put_report.sh')
REPORT_STEP = 'The report, written to this branch'
BRANCH = 'more-records'


def commands(chunk):
    return [line.strip() for line in chunk.split('run: |', 1)[1].splitlines()
            if line.strip().startswith(('python3', 'rm '))]


class TestBuildTestWorkflow(unittest.TestCase):
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
        self.assertNotIn('replace_release_files', self.text)
        self.assertIn(f'  push:\n    branches: [{BRANCH}]\n', self.text)
        self.assertIn('\n  workflow_dispatch:\n', self.text)

    def test_the_report_goes_to_the_branch_that_runs_it(self):
        self.assertEqual(self.steps[REPORT_STEP].count(f' {BRANCH} '), 1)

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
        pin = r"DSC_REF: \$\{\{ vars\.DSC_REF \|\| '([^']*)' \}\}"
        self.assertEqual(re.search(pin, self.text).group(1), re.search(pin, self.build).group(1))
        self.assertLess(self.text.index('"${#DSC_REF}" -eq 40'), self.text.index('actions/checkout@'))

    def test_the_build_is_run_with_its_own_commands_in_its_order(self):
        build = steps(self.build)
        ours = commands(self.steps["Fetch, the tables, the records, the keyed file (the build's commands)"])
        theirs = commands(build['Fetch, the tables, the records, the keyed file'])
        # Every one of the build's, in its order; between them only the report's own (more.py's counts kept).
        self.assertEqual([c for c in ours if c in theirs], theirs)
        self.assertEqual([c for c in ours if c not in theirs],
                         ['python3 -I builder/build_report.py stat out/more.db > out/more-stat.txt'])
        self.assertLess(ours.index('python3 -I builder/build_report.py stat out/more.db > out/more-stat.txt'),
                        ours.index('rm -f out/more.db'))
        self.assertEqual(commands(self.steps["The parts and their manifest (the build's commands)"])[0],
                         commands(build['The parts and their manifest'])[0])
        self.assertIn("MB: '32'", self.text)
        self.assertIn("MB: ${{ inputs.mb_part || '32' }}", self.build)

    def test_the_first_steps_checks_refuse_what_they_should(self):
        script = self.steps['The inputs, checked before anything runs'].split('run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        good = {'DSC_REF': 'a' * 40, 'DUMP': '20261114-002230'}

        def run(**change):
            env = {**good, **change, 'PATH': os.environ['PATH']}
            return subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True).returncode

        self.assertEqual(run(), 0)
        self.assertEqual(run(DUMP=''), 0)
        for change in ({'DSC_REF': 'dev'}, {'DSC_REF': 'a' * 39}, {'DUMP': '2026"; id; "'}, {'DUMP': 'latest'}):
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
            f.write("The build's file: Björk\n" * 50)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_put(self, *args):
        env = {'PATH': self.tmp + os.pathsep + os.environ['PATH'], 'GH_LOG': self.log}
        return subprocess.run(['sh', PUT, *args], env=env, capture_output=True, text=True)

    def test_it_writes_the_file_to_the_branch(self):
        r = self.run_put('o/r', BRANCH, 'reports/build-1-run2.txt', self.report)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.log) as f:
            a = f.read().splitlines()
        self.assertEqual(a[:3], ['api', '-X', 'PUT'])
        self.assertEqual(a[3], 'repos/o/r/contents/reports/build-1-run2.txt')
        fields = dict(x.split('=', 1) for x in a[4:] if '=' in x)
        self.assertEqual((fields['branch'], fields['message']), (BRANCH, 'build-1-run2.txt'))
        with open(self.report, 'rb') as f:
            self.assertEqual(base64.b64decode(fields['content']), f.read())

    def test_it_refuses_what_it_should(self):
        empty = os.path.join(self.tmp, 'empty.txt')
        open(empty, 'w').close()
        for args in (('or', BRANCH, 'reports/a.txt', self.report),
                     ('o/r', 'main; id', 'reports/a.txt', self.report),
                     ('o/r', '', 'reports/a.txt', self.report),
                     ('o/r', BRANCH, 'builder/a.txt', self.report),
                     ('o/r', BRANCH, 'a.txt', self.report),
                     ('o/r', BRANCH, 'reports/a.md', self.report),
                     ('o/r', BRANCH, 'reports/x/a.txt', self.report),
                     ('o/r', BRANCH, 'reports/a b.txt', self.report),
                     ('o/r', BRANCH, 'reports/a.txt', empty),
                     ('o/r', BRANCH, 'reports/a.txt', os.path.join(self.tmp, 'none.txt'))):
            with self.subTest(args=args):
                r = self.run_put(*args)
                self.assertEqual(r.returncode, 1, args)
                self.assertFalse(os.path.exists(self.log), args)


class TestBuildReport(More):
    def test_it_reports_every_table_the_parts_the_meta_and_the_counts(self):
        rel = self.t('rel-report')
        man = self.quiet(load_parts.parts, self.t('keyed-sized.db'), rel, 1_000)
        mpath = os.path.join(rel, f"load-{man['dump']}.json")
        with open(self.t('df.txt'), 'w') as f:
            f.write('after the fetch        /dev/root 72G 40G 32G 56% /\n')
        with open(self.t('stat.txt'), 'w', encoding='utf-8') as f:
            f.write(build_report.stat(self.more))
        text = build_report.report(self.t('keyed-sized.db'), mpath, self.t('df.txt'), self.t('stat.txt'))
        self.assertIn(f"dump {man['dump']}, data-2.", text)
        self.assertRegex(text, rf"\n  rows a load writes +{sum(man['tables'].values()):,}\n")
        self.assertRegex(text, rf"\n  parts +{len(man['parts']):,}\n")
        self.assertGreater(len(man['parts']), 1)
        self.assertRegex(text, rf'\n  manifest +{os.path.getsize(mpath):,} bytes\n')
        for k in keyed.KINDS + keyed.MORE_KINDS:
            self.assertRegex(text, rf'\n{k:6} {keyed.SIZED[k]:>4} {man["tables"][k]:>8,} ', k)
        self.assertNotIn('more_names', text)                            # the names are a table, never a meta row
        self.assertRegex(text, r'\nrol +1 +[12] ')
        self.assertIn('  records pgr', text)
        self.assertIn('  after the fetch        /dev/root 72G 40G 32G 56% /', text)
        out, argv = io.StringIO(), sys.argv
        try:
            with contextlib.redirect_stdout(out):
                sys.argv = ['build_report.py', 'stat', self.more]
                build_report.main()
        finally:
            sys.argv = argv
        self.assertEqual(out.getvalue(), build_report.stat(self.more))
        self.assertIn('records crd\t2\n', out.getvalue())


if __name__ == '__main__':
    unittest.main()
