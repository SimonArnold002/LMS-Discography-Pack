"""The build workflow's own rules (.github/workflows/build.yml), read as text: what runs here is this repo's code and
one fixed commit of LMS-Discography's Sources.pm, and the token that can publish a release reaches only the steps that
publish. No YAML library: the standard library only, as the builder."""
import os, re, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, '.github', 'workflows', 'build.yml')
TOKEN_STEPS = ['Which dump', 'The release', 'Older releases deleted (the newest KEEP stay)',
               'The last-run file, when the newest commit is two weeks old']


def steps(text):
    """{step name (or its `uses`): the step's text}, in the file's order."""
    body = text.split('\n    steps:\n', 1)[1]
    out = {}
    for chunk in re.split(r'\n(?=      - )', body):
        m = re.search(r'- name: (.+)', chunk) or re.search(r'- uses: (\S+)', chunk)
        if m:
            out.setdefault(m.group(1).strip(), chunk)
    return out


class TestWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PATH) as f:
            cls.text = f.read()
        cls.steps = steps(cls.text)

    def test_the_steps_are_found(self):
        self.assertGreaterEqual(len(self.steps), 9)
        for name in TOKEN_STEPS:
            self.assertIn(name, self.steps)

    def test_no_input_or_variable_is_pasted_into_a_script(self):
        # `${{ ... }}` inside a `run:` is text put into the script before the shell reads it: a value holding a quote
        # runs as code. A run block takes its values from the environment.
        for name, chunk in self.steps.items():
            if 'run:' in chunk:
                self.assertNotIn('${{', chunk.split('run:', 1)[1], name)

    def test_the_token_reaches_only_the_steps_that_publish(self):
        job_env = self.text.split('\n    steps:\n', 1)[0]
        self.assertNotIn('github.token', job_env)
        self.assertNotIn('secrets.', self.text)
        with_token = [n for n, c in self.steps.items() if 'github.token' in c or 'GH_TOKEN' in c.split('run:', 1)[0]]
        self.assertEqual(with_token, TOKEN_STEPS)
        for name, chunk in self.steps.items():
            if name not in TOKEN_STEPS:
                self.assertNotIn('gh ', chunk.split('run:', 1)[-1] if 'run:' in chunk else '', name)
            # No step that holds the token runs the build's Python or the name-key Perl.
            elif re.search(r'python3 (?!-I builder/fetch_dump\.py latest)|perl|keyed\.py|build_pack', chunk):
                self.fail(f'{name}: runs build code with the token')

    def test_it_runs_once_a_day_off_the_hour_and_by_hand(self):
        crons = re.findall(r"- cron: '([^']*)'", self.text)
        self.assertEqual(len(crons), 1)
        minute, hour, dom, month, dow = crons[0].split()
        self.assertTrue(minute.isdigit() and 5 <= int(minute) <= 55, minute)     # not at the start of an hour
        self.assertTrue(hour.isdigit() and 0 <= int(hour) <= 23, hour)
        self.assertEqual((dom, month, dow), ('*', '*', '*'))
        self.assertIn('\n  workflow_dispatch:\n', self.text)

    def test_the_last_run_step_is_last_and_runs_whether_or_not_a_dump_was_built(self):
        name = TOKEN_STEPS[-1]
        self.assertEqual(list(self.steps)[-1], name)
        chunk = self.steps[name]
        self.assertNotIn('if:', chunk)
        self.assertIn('run: sh builder/mark_run.sh "$GITHUB_REPOSITORY" 14 "$MARK"', chunk)
        self.assertIn('      MARK: ${{ inputs.mark }}', self.text.split('\n    steps:\n', 1)[0])

    def test_a_scheduled_run_has_every_value_it_needs(self):
        # A scheduled run has no inputs: each is empty. The first step's checks must pass with them empty wherever the
        # job gives no default of its own (the dump, force, mark), and mb_part and keep have theirs.
        job_env = self.text.split('\n    steps:\n', 1)[0]
        for name in ('mb_part', 'keep'):
            self.assertRegex(job_env, r"\$\{\{ inputs\.%s \|\| '\d+' \}\}" % name)

    def test_the_build_makes_the_more_file_at_each_kinds_own_count(self):
        # Two downloads, never on the disk together: today's tables made into the tables file and deleted (with what
        # was made from them) before the second download; its tables made into more.py's records and deleted before
        # the records are put together. The keyed file sized: no input sets one count for every kind.
        lines = [x.strip() for x in self.steps['Fetch, the tables, the records, the keyed file'].split('run: |', 1)[1]
                 .splitlines() if x.strip()]
        order = ['python3 -I builder/measure_extra.py fetch "$B" work',
                 'python3 -I builder/keyed.py aliases out/base.db "$PWD/dsc" out/aliases.tsv',
                 'rm -rf work',
                 'python3 -I builder/keyed.py narrow out/base.db out/facts.db out/aliases.tsv out/tables.db',
                 'rm -f out/base.db out/facts.db out/aliases.tsv',
                 'python3 -I builder/more.py fetch "$B" work',
                 'python3 -I builder/more.py entries work out/more.db',
                 'rm -rf work',
                 'python3 -I builder/keyed.py entries out/tables.db out/entries.db out/more.db',
                 'rm -f out/more.db',
                 'python3 -I builder/keyed.py keyed out/entries.db out/tables.db sized out/keyed.db']
        self.assertEqual([x for x in lines if x in order], order)
        self.assertEqual([x for x in lines if ' fetch ' in x], [order[0], order[5]])
        self.assertEqual([x for x in lines if x.startswith('python3')][:4],
                         ['python3 -I builder/measure_extra.py fetch "$B" work',
                          'python3 -I builder/build_pack.py work out/base.db --dsc "$PWD/dsc" --dump "$B"',
                          'python3 -I builder/measure_extra.py facts work out/facts.db > /dev/null',
                          order[1]])
        self.assertNotIn('inputs.bits', self.text)
        self.assertNotIn('BITS', self.text)

    def test_a_checkout_leaves_no_token_behind(self):
        # actions/checkout writes the token into .git/config unless told not to: every later step could read it.
        uses = [c for c in self.steps.values() if 'actions/checkout@' in c]
        self.assertEqual(len(uses), 2)
        for c in uses:
            self.assertIn('persist-credentials: false', c)

    def test_every_action_and_the_name_key_code_are_fixed_commits(self):
        for ref in re.findall(r'uses: \S+@(\S+)', self.text):
            self.assertRegex(ref, r'^[0-9a-f]{40}$')
        m = re.search(r"DSC_REF: \$\{\{ vars\.DSC_REF \|\| '([^']*)' \}\}", self.text)
        self.assertIsNotNone(m)
        self.assertRegex(m.group(1), r'^[0-9a-f]{40}$')
        # A repo variable may name another commit, never a branch: checked by the first step, before any checkout.
        first = list(self.steps.values())[0]
        self.assertIn('"${#DSC_REF}" -eq 40', first)
        self.assertIn('case "$DSC_REF" in *[!0-9a-f]*)', first)
        self.assertLess(self.text.index('"${#DSC_REF}" -eq 40'), self.text.index('actions/checkout@'))

    def test_the_first_steps_checks_refuse_what_they_should(self):
        # The first step's script itself, run by sh with made-up values.
        import subprocess
        script = self.steps['The inputs, checked before anything runs'].split('run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        good = {'DSC_REF': 'a' * 40, 'DUMP': '20261114-002230', 'MB': '32', 'KEEP': '2'}

        def run(**change):
            env = {**good, **change, 'PATH': os.environ['PATH']}
            return subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True).returncode

        self.assertEqual(run(), 0)
        self.assertEqual(run(DUMP=''), 0)                              # blank = the newest
        for change in ({'DSC_REF': 'dev'}, {'DSC_REF': 'a' * 39}, {'DSC_REF': 'a' * 41}, {'DSC_REF': 'A' * 40},
                       {'DSC_REF': ''}, {'DSC_REF': 'refs/heads/dev'}, {'DUMP': '2026"; id; "'}, {'DUMP': 'latest'},
                       {'MB': '32; id'}, {'MB': ''}, {'KEEP': '-1'}, {'KEEP': '2 3'}):
            with self.subTest(change=change):
                self.assertEqual(run(**change), 1)


if __name__ == '__main__':
    unittest.main()
