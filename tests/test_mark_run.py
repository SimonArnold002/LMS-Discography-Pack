"""builder/mark_run.sh with a stand-in `gh`: the file `last-run` is written only when the repository's newest commit is
older than the days given (or when told to write now), as a change to the file that is there or as a new file; a
question that fails writes nothing and fails the script. Run under every POSIX shell found."""
import base64, os, re, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'builder', 'mark_run.sh')
SHELLS = [s for s in dict.fromkeys(shutil.which(x) for x in ('sh', 'dash')) if s]

# Logs every call, one line each. The commits question prints GH_COMMITS (fails on GH_COMMITS_FAIL), the folder's
# listing prints GH_SHA (fails on GH_LIST_FAIL), the write fails on GH_PUT_FAIL.
GH = '''#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  *"-X PUT"*) [ -z "${GH_PUT_FAIL:-}" ] || exit 1; echo abc123 ;;
  *"/commits?since="*) [ -z "${GH_COMMITS_FAIL:-}" ] || exit 1; echo "${GH_COMMITS-0}" ;;
  *"/contents --jq"*) [ -z "${GH_LIST_FAIL:-}" ] || exit 1; [ -z "${GH_SHA:-}" ] || echo "$GH_SHA" ;;
  *) exit 9 ;;
esac
'''
PUT = re.compile(r'^api -X PUT repos/o/r/contents/last-run -f message=last-run (\S+) -f content=(\S+)( -f sha=(\S+))? --jq \.commit\.sha$')


class TestMarkRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_ = os.path.join(self.tmp, 'bin')
        os.mkdir(bin_)
        with open(os.path.join(bin_, 'gh'), 'w') as f:
            f.write(GH)
        os.chmod(os.path.join(bin_, 'gh'), 0o755)
        self.log = os.path.join(self.tmp, 'gh.log')
        self.env = dict(os.environ, PATH=bin_ + os.pathsep + os.environ['PATH'], GH_LOG=self.log)

    def run_script(self, sh, *args, **env):
        if os.path.exists(self.log):
            os.remove(self.log)
        p = subprocess.run([sh, SCRIPT, *args], env=dict(self.env, **env), capture_output=True, text=True, cwd=self.tmp)
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as f:
                calls = f.read().splitlines()
        return p.returncode, calls, p.stdout

    def each(self):
        self.assertTrue(SHELLS)
        for sh in SHELLS:
            with self.subTest(sh=sh):
                yield sh

    def test_a_recent_commit_and_nothing_is_written(self):
        for sh in self.each():
            rc, calls, out = self.run_script(sh, 'o/r', '14', GH_COMMITS='1')
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            self.assertRegex(calls[0], r'^api repos/o/r/commits\?since=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ&per_page=1 --jq length$')
            self.assertIn('nothing written', out)

    def test_the_question_asks_about_the_days_given(self):
        import datetime
        for sh in self.each():
            for days in (14, 3):
                _, calls, _ = self.run_script(sh, 'o/r', str(days), GH_COMMITS='1')
                since = datetime.datetime.strptime(re.search(r'since=([^&]+)', calls[0]).group(1), '%Y-%m-%dT%H:%M:%SZ')
                want = datetime.datetime.utcnow() - datetime.timedelta(days=days)
                self.assertLess(abs((since - want).total_seconds()), 120, days)

    def test_no_commit_in_the_days_given_writes_the_file(self):
        for sh in self.each():
            for sha in ('', 'f00d'):                                  # a new file; a change to the one there
                rc, calls, out = self.run_script(sh, 'o/r', '14', GH_COMMITS='0', GH_SHA=sha)
                self.assertEqual(rc, 0)
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1], 'api repos/o/r/contents --jq .[] | select(.name == "last-run") | .sha')
                m = PUT.match(calls[2])
                self.assertIsNotNone(m, calls[2])
                self.assertEqual(m.group(4), sha or None)
                self.assertRegex(m.group(1), r'^\d{4}-\d\d-\d\dT\d\d:\d\dZ$')
                self.assertEqual(base64.b64decode(m.group(2)).decode(), m.group(1) + '\n')
                self.assertIn('last-run written', out)

    def test_told_to_write_now_it_asks_nothing_about_the_commits(self):
        for sh in self.each():
            rc, calls, _ = self.run_script(sh, 'o/r', '14', 'true', GH_COMMITS='1')
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 2)
            self.assertIsNotNone(PUT.match(calls[1]), calls[1])
            for word in ('false', '', 'yes', 'TRUE'):                 # only `true` is now
                rc, calls, _ = self.run_script(sh, 'o/r', '14', word, GH_COMMITS='1')
                self.assertEqual((rc, len(calls)), (0, 1), word)

    def test_a_question_that_fails_writes_nothing_and_fails(self):
        for sh in self.each():
            for env, asked in (({'GH_COMMITS_FAIL': '1'}, 1), ({'GH_LIST_FAIL': '1'}, 2), ({'GH_COMMITS': ''}, 1),
                               ({'GH_COMMITS': 'Not Found'}, 1)):
                rc, calls, _ = self.run_script(sh, 'o/r', '14', **env)
                self.assertNotEqual(rc, 0, env)
                self.assertEqual(len(calls), asked, env)
                self.assertFalse(any('PUT' in c for c in calls), env)
            rc, calls, out = self.run_script(sh, 'o/r', '14', GH_PUT_FAIL='1')
            self.assertNotEqual(rc, 0)
            self.assertNotIn('last-run written', out)

    def test_what_is_not_a_repo_or_a_number_of_days_asks_nothing(self):
        for sh in self.each():
            for args in (('o/r', ''), ('o/r', '14d'), ('o/r', '-1'), ('repo', '14'), ('o/r', '1 4')):
                rc, calls, _ = self.run_script(sh, *args)
                self.assertEqual((rc, calls), (1, []), args)


if __name__ == '__main__':
    unittest.main()
