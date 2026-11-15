"""builder/replace_release_files.sh with a stand-in `gh` (a build run again re-publishes through it). Holds:
every file of the folder uploaded with --clobber BEFORE anything is deleted, then only the release files the folder
does not hold deleted; a failed upload, a failed asset listing or a failed delete fails the script (and so the workflow
step), the upload's and the listing's with nothing deleted. Run under every POSIX shell found (the runner's `sh` is
dash)."""
import os, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'builder', 'replace_release_files.sh')
SHELLS = [s for s in dict.fromkeys(shutil.which(x) for x in ('sh', 'dash')) if s]

# Logs every call; `release upload` fails on GH_UPLOAD_FAIL, `release view` prints GH_ASSETS (or fails on
# GH_VIEW_FAIL), `release delete-asset` fails on GH_DELETE_FAIL.
GH = '''#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "release upload") [ -z "${GH_UPLOAD_FAIL:-}" ] || exit 1 ;;
  "release view") [ -z "${GH_VIEW_FAIL:-}" ] || exit 1; cat "$GH_ASSETS" ;;
  "release delete-asset") [ -z "${GH_DELETE_FAIL:-}" ] || exit 1 ;;
esac
'''


class TestReplaceReleaseFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_ = os.path.join(self.tmp, 'bin')
        os.mkdir(bin_)
        with open(os.path.join(bin_, 'gh'), 'w') as f:
            f.write(GH)
        os.chmod(os.path.join(bin_, 'gh'), 0o755)
        self.dir = os.path.join(self.tmp, 'rel')
        os.mkdir(self.dir)
        for name in ('load-B.json', 'load-B-001.bin', 'load-B-002.bin'):
            open(os.path.join(self.dir, name), 'w').close()
        self.assets = os.path.join(self.tmp, 'assets')
        with open(self.assets, 'w') as f:                          # the release before: one part more
            f.write('load-B.json\nload-B-001.bin\nload-B-002.bin\nload-B-003.bin\n')
        self.log = os.path.join(self.tmp, 'gh.log')
        self.env = dict(os.environ, PATH=bin_ + os.pathsep + os.environ['PATH'], GH_LOG=self.log, GH_ASSETS=self.assets)

    def run_script(self, sh, **env):
        if os.path.exists(self.log):
            os.remove(self.log)
        p = subprocess.run([sh, SCRIPT, 'TAG', self.dir], env=dict(self.env, **env), capture_output=True, text=True)
        if not os.path.exists(self.log):                            # gh never called
            return p.returncode, []
        with open(self.log) as f:
            return p.returncode, f.read().splitlines()

    def test_uploads_first_then_deletes_only_the_stale_file(self):
        for sh in SHELLS:
            with self.subTest(sh=sh):
                rc, calls = self.run_script(sh)
                self.assertEqual(rc, 0)
                files = ' '.join(os.path.join(self.dir, n) for n in sorted(os.listdir(self.dir)))
                self.assertEqual(calls, [f'release upload TAG {files} --clobber',
                                         'release view TAG --json assets --jq .assets[].name',
                                         'release delete-asset TAG load-B-003.bin --yes'])

    def test_a_failed_upload_fails_the_script_and_deletes_nothing(self):
        # A stale file is deleted only once every upload is done: an upload that fails must end the script there.
        for sh in SHELLS:
            with self.subTest(sh=sh):
                rc, calls = self.run_script(sh, GH_UPLOAD_FAIL='1')
                self.assertNotEqual(rc, 0)
                self.assertEqual([c.split()[1] for c in calls], ['upload'])

    def test_a_failed_listing_fails_the_script_and_deletes_nothing(self):
        for sh in SHELLS:
            with self.subTest(sh=sh):
                rc, calls = self.run_script(sh, GH_VIEW_FAIL='1')
                self.assertNotEqual(rc, 0)
                self.assertEqual([c.split()[1] for c in calls], ['upload', 'view'])

    def test_a_name_github_would_change_is_refused_before_any_upload(self):
        # GitHub renames an asset whose name holds a space or another character outside [A-Za-z0-9._-] (a space becomes
        # '.'): the release would list it under its new name, the folder hold no such file, and the sweep delete it
        # straight after its upload. Refused before anything is sent. 'é' is caught under a UTF-8 locale
        # too, where a range ([a-z]) can hold it. GitHub renames a leading or trailing period too, and the upload's glob
        # skips a dotfile, so it would never be sent.
        for name in ('load B.json', 'load-*.bin', 'load-é.bin', 'load-B+1.bin', 'load-B.json.', '.load-B.json', '..load-B.bin'):
            with self.subTest(name=name):
                p = os.path.join(self.dir, name)
                open(p, 'w').close()
                try:
                    for sh in SHELLS:
                        for loc in ('C', 'en_US.UTF-8'):                # under en_US.UTF-8 bash 3.2's [a-z] holds 'é'
                            with self.subTest(sh=sh, loc=loc):
                                rc, calls = self.run_script(sh, LC_ALL=loc)
                                self.assertNotEqual(rc, 0)
                                self.assertEqual(calls, [])
                finally:
                    os.remove(p)

    def test_each_listed_name_is_one_name(self):
        # The listing read a line at a time, never split on blanks or expanded as a glob (`for a in $names`).
        with open(self.assets, 'a') as f:
            f.write('load B-004.bin\n*\n')
        for sh in SHELLS:
            with self.subTest(sh=sh):
                rc, calls = self.run_script(sh)
                self.assertEqual(rc, 0)
                self.assertEqual(calls[2:], ['release delete-asset TAG load-B-003.bin --yes',
                                             'release delete-asset TAG load B-004.bin --yes',
                                             'release delete-asset TAG * --yes'])

    def test_a_failed_delete_fails_the_script(self):
        for sh in SHELLS:
            with self.subTest(sh=sh):
                rc, calls = self.run_script(sh, GH_DELETE_FAIL='1')
                self.assertNotEqual(rc, 0)
                self.assertEqual(calls[-1], 'release delete-asset TAG load-B-003.bin --yes')


if __name__ == '__main__':
    unittest.main()
