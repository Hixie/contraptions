"""Tests for review_stage. They need git and an APFS temporary directory."""

import contextlib
import ctypes
import glob
import io
import os
import re
import struct
import subprocess
import tempfile
import unittest

_home = tempfile.mkdtemp()
_config = os.path.join(_home, "gitconfig")
open(_config, "w").close()
os.environ.update({
    "GIT_CONFIG_GLOBAL": _config, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@invalid",
})

import review_stage  # noqa: E402

LINES = "".join(f"line {i}\n" for i in range(1, 41))
EMPTY = "objects/e6/9de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True,
                          capture_output=True).stdout.decode()


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def read(root, rel, mode="r"):
    with open(os.path.join(root, rel), mode) as f:
        return f.read()


class AttrList(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_ushort),
                ("reserved", ctypes.c_ushort)] + [
        (name, ctypes.c_uint32)
        for name in ("common", "volume", "dir", "file", "extended")]


def clone_id(path):
    """The APFS clone identity of a file, which clones share and which
    increases as files are written."""
    attrs = AttrList(5, 0, 0, 0, 0, 0, 0x100)  # ATTR_CMNEXT_CLONEID
    buf = ctypes.create_string_buffer(64)
    # FSOPT_NOFOLLOW | FSOPT_ATTR_CMN_EXTENDED
    if ctypes.CDLL(None).getattrlist(os.fsencode(path), ctypes.byref(attrs),
                                     buf, 64, 0x21) != 0:
        raise OSError(path)
    return struct.unpack_from("<IQ", buf.raw)[1]


def times(root):
    """Each file's modification and birth times and each directory's birth
    time, leaving out .git. (Cloning a directory sets its modification
    time to the time of cloning.)"""
    out = {}
    for top, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in dirs:
            path = os.path.join(top, name)
            out[os.path.relpath(path, root)] = (os.lstat(path).st_birthtime,)
        for name in files:
            path = os.path.join(top, name)
            s = os.lstat(path)
            out[os.path.relpath(path, root)] = (s.st_mtime, s.st_birthtime)
    return out


class Staging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.mkdir(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, ".gitignore", "build/\n")
        write(self.repo, "a.txt", LINES)
        write(self.repo, "dir/b.txt", "b\n")
        write(self.repo, "gone.txt", "gone\n")
        write(self.repo, "c.txt", LINES)
        write(self.repo, "swap", "a file\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        write(self.repo, "build/output.txt", "built\n")
        write(self.repo, "cache/.gitignore", "*\n")
        write(self.repo, "cache/data.txt", "cached\n")
        os.makedirs(os.path.join(self.repo, "build", "nested", ".git"))
        write(self.repo, "build/nested/inner.txt", "inner\n")
        os.makedirs(os.path.join(self.repo, "other", ".git"))

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, out, *args, names=("🦜", "🦆")):
        with contextlib.redirect_stdout(io.StringIO()):
            return review_stage.stage(review_stage.parse(
                ["-C", self.repo, *args, os.path.join(self.tmp.name, out),
                 *names]))

    def change_worktree(self):
        write(self.repo, "a.txt", LINES.replace("line 3\n", "line three\n"))
        os.remove(os.path.join(self.repo, "gone.txt"))
        write(self.repo, "dir/new.txt", "new\n")
        os.remove(os.path.join(self.repo, "swap"))
        write(self.repo, "swap/inner.txt", "a directory\n")

    def test_reviewers_cannot_tell_which_version_came_first(self):
        self.change_worktree()
        git(self.repo, "update-index", "--assume-unchanged", "a.txt")
        state = (git(self.repo, "status", "--porcelain"),
                 git(self.repo, "rev-parse", "HEAD"),
                 read(self.repo, ".git/index", "rb"))
        out = os.path.join(self.tmp.name, "out")
        assignment = self.stage("out", "--base", "HEAD",
                                "--copy-ignored", "build")
        # Before anything reads the trees, every file in them has the
        # same access time.
        self.assertEqual(len({os.lstat(os.path.join(top, n)).st_atime_ns
                              for top, dirs, files in os.walk(out)
                              if ".git" not in top.split(os.sep)
                              for n in files if n != "README.md"
                              and n != "patch.diff"}), 1)
        self.assertEqual(sorted(assignment.values()), [False, True])
        self.assertEqual(sorted(os.listdir(out)), sorted(assignment))
        self.assertEqual(state, (
            git(self.repo, "status", "--porcelain"),
            git(self.repo, "rev-parse", "HEAD"),
            read(self.repo, ".git/index", "rb")))

        seen = {}
        for name, forward in assignment.items():
            place = os.path.join(out, name)
            self.assertEqual(sorted(os.listdir(place)),
                             ["README.md", "after", "before", "patch.diff"])
            before, after = (os.path.join(place, "before"),
                             os.path.join(place, "after"))
            stamp = times(before)["a.txt"]
            self.assertEqual(stamp[0], stamp[1])
            for tree in (before, after):
                stat = git(tree, "ls-files", "--debug")
                self.assertEqual(
                    set(re.findall(r"(?:time|dev|ino|uid|gid): (\S+)", stat)),
                    {"0:0", "0"}, stat)
                # The index and the empty object, which `add -N` writes
                # for files to be added, are written for the reviewer.
                inside = {p: v for p, v in times(
                    os.path.join(tree, ".git")).items()
                    if p not in ("index", "objects/e6", EMPTY)}
                self.assertEqual({v[-1] for v in inside.values()},
                                 {stamp[1]}, inside)
                self.assertEqual({v[0] for v in inside.values() if len(v) > 1},
                                 {stamp[0]}, inside)
            new, old = (after, before) if forward else (before, after)
            self.assertEqual(read(old, "a.txt"), LINES)
            self.assertIn("line three\n", read(new, "a.txt"))
            self.assertTrue(os.path.exists(os.path.join(old, "gone.txt")))
            self.assertFalse(os.path.exists(os.path.join(new, "gone.txt")))
            self.assertFalse(os.path.exists(os.path.join(old, "dir/new.txt")))
            self.assertEqual(read(new, "dir/new.txt"), "new\n")
            for tree in (before, after):
                self.assertEqual(read(tree, "build/output.txt"), "built\n")
                self.assertFalse(os.path.exists(os.path.join(tree, "cache")))
                self.assertFalse(os.path.exists(os.path.join(tree, "other")))

            patch = read(place, "patch.diff")
            self.assertNotIn("diff --git b/", patch)
            self.assertIn("--- a/a.txt\n+++ b/a.txt\n", patch)
            self.assertIn(("-line 3\n+line three\n" if forward
                           else "-line three\n+line 3\n"), patch)
            self.assertEqual(git(after, "diff", "HEAD"), patch)
            self.assertIn("deleted file", patch)
            self.assertEqual(git(before, "status", "--porcelain"), "")

            for tree in (before, after):
                self.assertNotIn("Test", read(tree, ".git/logs/HEAD"))
                log = git(tree, "log", "--all", "--format=%an %ae %ad %cd %s")
                seen.setdefault("log", set()).add(log)
                self.assertEqual(len(log.splitlines()), 1)
                self.assertEqual(git(tree, "rev-parse", "HEAD^{tree}"),
                                 git(before, "rev-parse", "HEAD^{tree}"))
            seen.setdefault("readme", set()).add(read(place, "README.md"))
            pack = [line.split() for line in git(
                before, "verify-pack", "-v", *glob.glob(os.path.join(
                    before, ".git/objects/pack/*.idx"))).splitlines()
                if re.match(r"[0-9a-f]{40} ", line)]
            self.assertEqual([len(o) for o in pack], [5] * len(pack))
            self.assertEqual([o[0] for o in pack], sorted(o[0] for o in pack))

            a, b = times(before), times(after)
            for path in set(a) & set(b):
                # A file in one tree can be a directory in the other.
                kept = min(len(a[path]), len(b[path]))
                self.assertEqual(a[path][-kept:], b[path][-kept:], path)
            shared = [".gitignore", "c.txt", "dir/b.txt", "build/output.txt"]
            for path in shared:
                self.assertEqual(clone_id(os.path.join(before, path)),
                                 clone_id(os.path.join(after, path)), path)
            for tree, own in ((old, ["a.txt", "gone.txt"]),
                              (new, ["a.txt", "dir/new.txt"])):
                self.assertGreater(
                    min(clone_id(os.path.join(tree, p)) for p in own),
                    max(clone_id(os.path.join(tree, p)) for p in shared))
            for path in set(a) ^ set(b):
                self.assertEqual({**a, **b}[path], stamp, path)
            seen[forward] = (a, b)
        self.assertEqual(len(seen["log"]), 1)
        self.assertEqual(len(seen["readme"]), 1)
        self.assertEqual(seen[True], seen[False][::-1])

    def test_later_rounds_list_only_the_hunks_that_changed(self):
        write(self.repo, "a.txt", LINES.replace("line 3\n", "line three\n")
              .replace("line 30\n", "line thirty\n"))
        git(self.repo, "mv", "c.txt", "é c.txt")
        write(self.repo, "é c.txt", LINES.replace("line 5\n", "line five\n"))
        git(self.repo, "commit", "-q", "-am", "change")
        first = self.stage("one", "--base", "HEAD~1", "--head", "HEAD")
        write(self.repo, "a.txt", read(self.repo, "a.txt").replace(
            "line thirty\n", "line thirty\nline thirty-one\n"))
        write(self.repo, "é c.txt", read(self.repo, "é c.txt").replace(
            "line 35\n", "line thirty-five\n"))
        git(self.repo, "commit", "-q", "-am", "fix")
        assignment = self.stage("two", "--base", "HEAD~2", "--head", "HEAD",
                                "--since-last", names=("🦜", "🦆", "🐓"))
        self.assertEqual({n: assignment[n] for n in first}, first)
        for name, forward in assignment.items():
            place = os.path.join(self.tmp.name, "two", name)
            self.assertIn(review_stage.CHANGES, read(place, "README.md"))
            self.assertEqual(read(place, review_stage.CHANGES), (
                "a.txt: before lines 27-33, after lines 27-34\n"
                "é c.txt: before lines 32-38, after lines 32-38\n" if forward
                else "a.txt: before lines 27-34, after lines 27-33\n"
                "c.txt: before lines 32-38, after lines 32-38\n"))
        assignment = self.stage("three", "--base", "HEAD~2", "--head",
                                "HEAD", "--since-last")
        for name in assignment:
            self.assertEqual(
                read(os.path.join(self.tmp.name, "three", name),
                     review_stage.CHANGES),
                "Nothing in the patch has changed.\n")
        self.assertNotIn(review_stage.CHANGES, os.listdir(
            os.path.join(self.tmp.name, "one", name)))

    def test_a_change_of_attributes_rewrites_every_file(self):
        def stage(out, adds):
            for name, forward in self.stage(out, "--base", "HEAD").items():
                place = os.path.join(self.tmp.name, out, name)
                crlf, lf = (("after", "before") if forward == adds
                            else ("before", "after"))
                self.assertIn(b"line 1\r\n", read(place, crlf + "/a.txt",
                                                   "rb"))
                self.assertNotIn(b"\r", read(place, lf + "/a.txt", "rb"))

        write(self.repo, ".gitattributes", "*.txt text eol=crlf\n")
        stage("adds", True)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "attributes")
        os.remove(os.path.join(self.repo, ".gitattributes"))
        stage("removes", False)

    def test_files_are_written_with_the_repository_attributes(self):
        write(self.repo, ".gitattributes", "a.txt text eol=crlf\n")
        git(self.repo, "add", ".gitattributes")
        git(self.repo, "commit", "-q", "-m", "attributes")
        write(self.repo, ".git/info/attributes", "c.txt text eol=crlf\n")
        self.change_worktree()
        for name in self.stage("out", "--base", "HEAD"):
            place = os.path.join(self.tmp.name, "out", name)
            for tree in ("before", "after"):
                for path in ("a.txt", "c.txt"):
                    self.assertIn(b"line 1\r\n", read(os.path.join(
                        place, tree), path, "rb"))
            self.assertEqual(git(os.path.join(place, "before"), "status",
                                 "--porcelain"), "")
            self.assertEqual(git(os.path.join(place, "after"), "diff",
                                 "HEAD"), read(place, "patch.diff"))

    def test_unnormalized_files_and_textconv_do_not_get_in_the_way(self):
        with open(os.path.join(self.repo, "crlf.txt"), "wb") as f:
            f.write(b"one\r\ntwo\r\n")
        git(self.repo, "add", "crlf.txt")
        git(self.repo, "commit", "-q", "-m", "crlf")
        write(self.repo, ".gitattributes", "* text\n*.dat diff=upper\n")
        write(self.repo, "x.dat", "hello\n")
        git(self.repo, "add", ".gitattributes", "x.dat")
        git(self.repo, "commit", "-q", "-m", "attributes")
        git(self.repo, "config", "diff.upper.textconv", "tr a-z A-Z <")
        write(self.repo, "x.dat", "world\n")
        self.stage("out", "--base", "HEAD")
        backup = git(self.repo, "rev-parse", "--git-path",
                     "review-stage.patch").strip()
        git(self.repo, "apply", "--check", "-R",
            os.path.join(self.repo, backup))

    def test_the_tree_check_finds_wrong_and_stray_files(self):
        index = os.path.join(self.tmp.name, "index")
        with open(index, "wb") as f:
            f.write(read(self.repo, ".git/index", "rb"))
        env = review_stage.at(os.path.join(self.repo, ".git"), self.repo,
                              index)
        tree = {line.split(b"\t", 1)[1]: line for line in review_stage.split(
            subprocess.run(["git", "-C", self.repo, "ls-tree", "-r", "-z",
                            "HEAD"], check=True,
                           capture_output=True).stdout)}
        review_stage.check(self.repo, env, tree, ["build", "cache"])
        with self.assertRaisesRegex(review_stage.Failure,
                                    "stray cache/.gitignore"):
            review_stage.check(self.repo, env, tree,
                               ["build", "cache/data.txt"])
        write(self.repo, "stray.txt", "stray\n")
        with self.assertRaisesRegex(review_stage.Failure, "stray stray.txt"):
            review_stage.check(self.repo, env, tree, ["build", "cache"])
        os.remove(os.path.join(self.repo, "stray.txt"))
        os.chmod(os.path.join(self.repo, "a.txt"), 0o755)
        with self.assertRaisesRegex(review_stage.Failure,
                                    "a.txt as 100755, not 100644"):
            review_stage.check(self.repo, env, tree, ["build", "cache"])
        os.remove(os.path.join(self.repo, "a.txt"))
        with self.assertRaisesRegex(review_stage.Failure, "lacks a.txt"):
            review_stage.check(self.repo, env, tree, ["build", "cache"])
        os.mkdir(os.path.join(self.repo, "a.txt"))
        with self.assertRaisesRegex(review_stage.Failure,
                                    "a.txt as something else"):
            review_stage.check(self.repo, env, tree, ["build", "cache"])
        os.rmdir(os.path.join(self.repo, "a.txt"))
        write(self.repo, "a.txt", LINES)
        os.rename(os.path.join(self.repo, "dir"),
                  os.path.join(self.repo, "moved"))
        write(self.repo, "dir", "a file\n")
        with self.assertRaisesRegex(review_stage.Failure, "lacks dir/b.txt"):
            review_stage.check(self.repo, env, tree, ["build", "cache",
                                                      "moved"])

    def test_mistakes_fail_without_leaving_anything_behind(self):
        cases = [
            ("out", ["--base", "HEAD"], "nothing to review"),
            ("out", ["--base", "HEAD", "--since-last"],
             "nothing has been staged"),
            ("out", [], "--base"),
            ("repo/out", ["--base", "HEAD"], "inside the worktree"),
            ("missing/out", ["--base", "HEAD"], "cannot create"),
            ("out", ["--copy-ignored", "a.txt"], "does not ignore"),
            ("out", ["--copy-ignored", "nothing"], "no such"),
            ("out", ["--copy-ignored", "../x"], "not inside"),
            ("out", ["--base=-x"], "not a revision"),
        ]
        for out, args, message in cases:
            with self.assertRaisesRegex(review_stage.Failure, message):
                self.stage(out, *args)
            self.assertFalse(os.path.exists(os.path.join(self.tmp.name, out)))
        with self.assertRaisesRegex(review_stage.Failure, "distinct"):
            self.stage("out", "--base", "HEAD", names=("🦜", "🦜"))
        self.change_worktree()
        self.stage("out", "--base", "HEAD")
        with self.assertRaisesRegex(review_stage.Failure, "cannot create"):
            self.stage("out", "--base", "HEAD")
        head = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "update-index", "--add", "--cacheinfo",
            f"160000,{head},sub")
        git(self.repo, "commit", "-q", "-m", "submodule")
        with self.assertRaisesRegex(review_stage.Failure, "submodules"):
            self.stage("sub", "--base", "HEAD~1", "--head", "HEAD")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "sub")))


class Hunks(unittest.TestCase):
    def test_descriptions(self):
        patch = (b"diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n"
                 b"@@ -0,0 +1 @@\n+one\n"
                 b"@@ -5,2 +6,0 @@ context\n-a\n-b\n"
                 b"diff --git a/y b/y\nindex 3..4 100644\n"
                 b"Binary files a/y and b/y differ\n")
        self.assertEqual(review_stage.changed_hunks(b"", patch), [
            "x: no before lines, after line 1",
            "x: before lines 5-6, no after lines",
            "y: the whole file"])
        quoted = (b'diff --git "a/\\tab" "b/\\tab"\nindex 5..6 100644\n'
                  b'Binary files "a/\\tab" and "b/\\tab" differ\n')
        self.assertEqual(review_stage.changed_hunks(b"", quoted),
                         ["\tab: the whole file"])
        moved = patch.replace(b"@@ -5,2 +6,0 @@ context",
                              b"@@ -9,2 +10,0 @@ other")
        self.assertEqual(review_stage.changed_hunks(patch, moved), [])
        rebinary = patch.replace(b"index 3..4", b"index 3..5")
        self.assertEqual(review_stage.changed_hunks(patch, rebinary),
                         ["y: the whole file"])


if __name__ == "__main__":
    unittest.main()
