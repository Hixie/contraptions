#!/usr/bin/env python3
"""Stage a change in a git worktree for reviewers who must not be able to
tell which version came first. See README.md."""

import argparse
import ast
import ctypes
import errno
import functools
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import time

IDENTITY = {
    "GIT_AUTHOR_NAME": "review-stage",
    "GIT_AUTHOR_EMAIL": "review-stage@invalid",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_NAME": "review-stage",
    "GIT_COMMITTER_EMAIL": "review-stage@invalid",
    "GIT_COMMITTER_DATE": "@0 +0000",
}
DIFF = ["-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
        "--no-relative", "-M", "--src-prefix=a/", "--dst-prefix=b/"]
CHANGES = "changed-since-previous-round.txt"


class Failure(Exception):
    pass


_libc = ctypes.CDLL(None, use_errno=True)
_random = random.SystemRandom()


@functools.lru_cache(maxsize=None)
def environment():
    """The environment for git, without the variables that would point it
    at some other repository."""
    env = dict(os.environ)
    for name in subprocess.run(["git", "rev-parse", "--local-env-vars"],
                               capture_output=True, text=True,
                               check=True).stdout.split():
        env.pop(name, None)
    return env


def clone(src, dst):
    """Copy a file or a whole directory tree with clonefile(2), so that the
    copy shares its data blocks with the original and keeps its times."""
    if _libc.clonefile(os.fsencode(src), os.fsencode(dst), 1) != 0:
        err = ctypes.get_errno()
        hint = " (both must be on the same APFS volume)" if err in (
            errno.EXDEV, errno.ENOTSUP) else ""
        raise Failure(f"cannot clone {src} to {dst}: {os.strerror(err)}"
                      + hint)


def git(*args, input=None, env=None):
    result = subprocess.run(["git", *args], input=input,
                            capture_output=True,
                            env={**environment(), **(env or {})})
    if result.returncode != 0:
        raise Failure(f"git {' '.join(args)}: "
                      + (result.stderr + result.stdout).decode(
                          errors="replace").strip())
    return result.stdout


def text(*args, **kwargs):
    return os.fsdecode(git(*args, **kwargs)).strip()


def split(output):
    return [p for p in output.split(b"\0") if p]


def at(git_dir, work_tree, index=None):
    """The environment for running git on one git directory and tree."""
    env = {"GIT_DIR": git_dir, "GIT_WORK_TREE": work_tree}
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


class Source:
    """The worktree holding the change."""

    def __init__(self, path):
        self.path = text("-C", path, "rev-parse", "--show-toplevel")
        self.git_dir = self.text("rev-parse", "--absolute-git-dir")

    def git(self, *args, **kwargs):
        return git("-C", self.path, *args, **kwargs)

    def text(self, *args, **kwargs):
        return text("-C", self.path, *args, **kwargs)

    def tree(self, rev):
        if rev.startswith("-"):
            raise Failure(f"{rev} is not a revision")
        return self.text("rev-parse", "--verify", rev + "^{tree}")

    def git_path(self, name):
        return os.path.join(self.path, self.text("rev-parse", "--git-path",
                                                 name))

    def worktree_tree(self, scratch):
        """The tree of the working tree as it is: tracked files with their
        current contents plus untracked files that are not ignored, leaving
        out nested repositories. The real index is not touched."""
        index = os.path.join(scratch, "worktree-index")
        if os.path.exists(self.git_path("index")):
            shutil.copyfile(self.git_path("index"), index)
        env = {"GIT_INDEX_FILE": index}
        assumed = [line[2:] for line in split(self.git(
            "ls-files", "-z", "-v", env=env)) if line[:1].islower()]
        if assumed:
            self.git("update-index", "--no-assume-unchanged", "-z", "--stdin",
                     input=b"\0".join(assumed), env=env)
        self.git("add", "-u", env=env)
        untracked = [p for p in split(self.git(
            "ls-files", "-z", "-o", "--exclude-standard"))
            if not p.endswith(b"/")]
        if untracked:
            self.git("--literal-pathspecs", "add",
                     "--pathspec-from-file=-", "--pathspec-file-nul",
                     input=b"\0".join(untracked), env=env)
        return self.text("write-tree", env=env)

    def ignores(self, rel):
        return subprocess.run(
            ["git", "-C", self.path, "check-ignore", "-q", "--", rel],
            env=environment()).returncode == 0

    def diff(self, before, after, *extra):
        return self.git(*DIFF, *extra, before, after)


def stamp(root, paths, when):
    """Give each path that exists, and every directory above it, the same
    modification and birth times. (Setting a modification time earlier
    than a file's birth time on APFS sets the birth time to it too.)"""
    owned = {root}
    for rel in paths:
        while rel:
            owned.add(os.path.join(root, rel))
            rel = os.path.dirname(rel)
    for path in owned:
        if os.path.lexists(path):
            os.utime(path, (when, when), follow_symlinks=False)


def copy_new(src, dst):
    """Copy a file or symbolic link into newly allocated storage, in place
    of any file there already."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        os.remove(dst)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
        return
    with open(src, "rb") as f, open(dst, "xb") as g:
        shutil.copyfileobj(f, g)
    os.chmod(dst, os.stat(src).st_mode & 0o777)


def build_masters(source, base, head, copied, scratch):
    """Write one tree and one git directory for each version, from which
    each reviewer's trees are cloned.

    The two trees are built the same way, so that nothing about their
    files says which version was written first. The files the versions
    share, and the copied ignored paths, are written once and cloned into
    both trees, so those files have the same storage and clone identity
    in both. Each version's other files are then written into newly
    allocated storage, in one random order across both trees.
    If the change touches a .gitattributes file, which decides how files
    are written out, no files count as shared. Every path that is not
    shared then gets the same times in both trees, and so does everything
    in both git directories. Both git directories hold the objects of
    both versions, so they differ only in their one commit."""
    changes = source.git("diff-tree", "-r", "-z", "--no-renames",
                         "--name-status", base, head).split(b"\0")[:-1]
    changes = list(zip(changes[::2], changes[1::2]))
    entries = {}
    for version in (base, head):
        entries[version] = {}
        for line in split(source.git("ls-tree", "-r", "-z", "--full-tree",
                                     version)):
            entries[version][line.split(b"\t", 1)[1]] = line
            if line.split(b" ", 2)[1] == b"commit":
                raise Failure("cannot stage submodules, such as "
                              + os.fsdecode(line.split(b"\t", 1)[1]))
    shared = set(entries[base].items()) & set(entries[head].items())
    if any(os.path.basename(p) == b".gitattributes" for _, p in changes):
        shared = set()
    own = {v: [line for p, line in entries[v].items()
               if (p, line) not in shared] for v in (base, head)}
    when = int(time.time())
    # Every step made once for each version takes the versions in this
    # order, so that the order in which things were written says nothing.
    order = [base, head]
    _random.shuffle(order)
    trees = {v: os.path.join(scratch, v) for v in (base, head)}
    dirs = {v: os.path.join(scratch, v + ".git") for v in (base, head)}

    objects = os.path.join(scratch, "objects.git")
    git("init", "-q", "--bare", "--initial-branch=before", objects)
    git("-C", objects, "config", "core.bare", "false")
    # Objects in hash order, and with no deltas, so that neither the order
    # of the pack nor which object is stored as a change to which says
    # which version came first.
    listed = source.git("rev-list", "--objects", base, head).splitlines()
    git("-C", objects, "index-pack", "--stdin", input=source.git(
        "pack-objects", "-q", "--stdout", "--no-reuse-delta", "--window=0",
        input=b"".join(sorted(line.split(b" ", 1)[0] + b"\n"
                              for line in listed))))
    # The source repository's own settings for which files to ignore and
    # how to write them out, so that git in the trees sees what it would.
    for name in ("info/exclude", "info/attributes"):
        if os.path.exists(source.git_path(name)):
            shutil.copyfile(source.git_path(name),
                            os.path.join(objects, name))
    items = split(source.git("config", "-z", "--list", "--show-scope"))
    for scope, item in zip(items[::2], items[1::2]):
        key, _, value = item.partition(b"\n")
        if scope in (b"local", b"worktree") and re.fullmatch(
                rb"core\.(autocrlf|eol)|(filter|diff)\..+", key):
            git("-C", objects, "config", "--add", os.fsdecode(key),
                os.fsdecode(value))
    for version in order:
        clone(objects, dirs[version])
        commit = text("-C", dirs[version], "commit-tree", "--no-gpg-sign",
                      "-m", "The version in before/", version, env=IDENTITY)
        git("-C", dirs[version], "update-ref", "refs/heads/before", commit,
            env=IDENTITY)

    common = os.path.join(scratch, "shared")
    os.mkdir(common)
    for rel in copied:
        os.makedirs(os.path.dirname(os.path.join(common, rel)), exist_ok=True)
        clone(os.path.join(source.path, rel), os.path.join(common, rel))
    shared_index = os.path.join(scratch, "shared.index")
    env = at(source.git_dir, common, shared_index)
    git("update-index", "-z", "--index-info",
        input=b"".join(line + b"\0" for _, line in shared), env=env)
    git("checkout-index", "-a", "-f", "-u", env=env)

    # Each version's other files are checked out with its whole index, so
    # that its .gitattributes files apply to them.
    index = {v: os.path.join(scratch, v + ".index") for v in (base, head)}
    names = {v: [line.split(b"\t", 1)[1] for line in own[v]]
             for v in (base, head)}
    writes = []
    for version in order:
        clone(common, trees[version])
        shutil.copyfile(shared_index, index[version])
        files = os.path.join(scratch, version + ".files")
        os.mkdir(files)
        env = at(source.git_dir, files, index[version])
        git("update-index", "-z", "--index-info",
            input=b"".join(line + b"\0" for line in own[version]), env=env)
        git("checkout-index", "-f", "-z", "--stdin",
            input=b"".join(p + b"\0" for p in names[version]), env=env)
        writes += [(files, trees[version], os.fsdecode(p))
                   for p in names[version]]
    _random.shuffle(writes)
    for files, tree, rel in writes:
        copy_new(os.path.join(files, rel), os.path.join(tree, rel))

    for version in order:
        stamp(trees[version], [os.fsdecode(p) for v in (base, head)
                               for p in names[v]], when)
        check(trees[version], at(source.git_dir, trees[version],
                                 index[version]), entries[version], copied)
    for git_dir in dirs.values():
        stamp(git_dir, [os.path.relpath(os.path.join(top, name), git_dir)
                        for top, subdirs, files in os.walk(git_dir)
                        for name in subdirs + files], when)
    later = time.time_ns()
    for root in list(trees.values()) + list(dirs.values()):
        settle(root, later)
    return trees, dirs, changes


def settle(root, later):
    """Give every path under root the same access time, later than any
    modification time, so that no access time depends on when a file
    was written or read during the staging."""
    for top, subdirs, files in os.walk(root):
        for path in [top] + [os.path.join(top, n) for n in subdirs + files]:
            os.utime(path, ns=(later, os.lstat(path).st_mtime_ns),
                     follow_symlinks=False)


def check(tree, env, entries, copied):
    """Fail unless tree holds exactly the version's paths, each a file, an
    executable file or a symbolic link as the version records, and the
    copied ignored paths."""
    for rel, line in entries.items():
        rel, mode = os.fsdecode(rel), line.split(b" ", 1)[0].decode()
        try:
            st = os.lstat(os.path.join(tree, rel))
        except OSError:
            raise Failure(f"staged tree {tree} lacks {rel}")
        kind = ("120000" if stat.S_ISLNK(st.st_mode)
                else "something else" if not stat.S_ISREG(st.st_mode)
                else "100755" if st.st_mode & stat.S_IXUSR else "100644")
        if kind != mode:
            raise Failure(f"staged tree {tree} has {rel} as {kind}, not"
                          f" {mode}")
    for entry in split(git("ls-files", "-z", "-o", env=env)):
        entry = os.fsdecode(entry).rstrip("/")
        if not any(entry == c or entry.startswith(c + "/") for c in copied):
            raise Failure(f"staged tree {tree} has a stray {entry}")


def build_tree(master, git_dir, dst, added):
    """Clone a master tree and git directory into dst, and give it a new
    index holding the commit, with the files only dst has as intent to
    add, so that `git diff` shows the whole change. The index has no
    record of any file's times or inode, so it holds nothing from the
    staging; git fills those in the first time it runs there."""
    clone(master, dst)
    clone(git_dir, os.path.join(dst, ".git"))
    git("-C", dst, "read-tree", "HEAD")
    if added:
        git("-C", dst, "--literal-pathspecs", "add", "-N", "-f",
            "--pathspec-from-file=-", "--pathspec-file-nul",
            input=b"\0".join(added))


HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def hunks(patch):
    """Split a patch into (file, key, line numbers) triples, one per hunk.
    The key is the file's header and the hunk's lines, without the line
    numbers or the index and similarity lines, which change whenever
    anything else in the file does. A file section with no hunks (a
    binary or mode change) keeps its index line and is one item with no
    line numbers."""
    out = []
    for section in re.split(rb"(?m)^(?=diff --git )", patch):
        if not section:
            continue
        head, *parts = re.split(rb"(?m)^(?=@@ )", section)
        lines = head.splitlines(keepends=True)
        name = file_name(lines)
        if not parts:
            out.append((name, head, None))
            continue
        key = b"".join(l for l in lines if not l.startswith(
            (b"index ", b"similarity index ", b"dissimilarity index ")))
        out.extend((name, key + p.split(b"\n", 1)[-1], HUNK.match(p))
                   for p in parts)
    return out


QUOTED = re.compile(rb'"(?:[^"\\]|\\.)*"')


def unquote(name):
    """Undo the quoting git applies to unusual file names."""
    if not QUOTED.fullmatch(name):
        return name
    return ast.literal_eval("b" + "".join(
        chr(c) if c < 128 else f"\\x{c:02x}" for c in name))


def file_name(lines):
    """The name of the file a patch section is about: the name after the
    change, unless the change deletes the file."""
    names = {}
    for line in lines:
        for prefix in (b"+++ ", b"--- ", b"rename to "):
            if line.startswith(prefix):
                names[prefix] = unquote(
                    line[len(prefix):].rstrip(b"\n").rstrip(b"\t"))
    for prefix in (b"+++ ", b"--- "):
        if names.get(prefix, b"/dev/null") != b"/dev/null":
            return os.fsdecode(names[prefix][2:])
    if b"rename to " in names:
        return os.fsdecode(names[b"rename to "])
    pair = lines[0][len(b"diff --git "):].rstrip(b"\n")
    quoted = QUOTED.match(pair)
    if quoted:
        return os.fsdecode(unquote(quoted.group())[2:])
    half = len(pair) // 2
    if pair[half:half + 3] == b" b/" and pair[2:half] == pair[half + 3:]:
        return os.fsdecode(pair[2:half])
    return os.fsdecode(pair)


def describe(name, match):
    if not match:
        return name + ": the whole file"

    def span(side, start, count):
        start, count = int(start), 1 if count is None else int(count)
        if count == 0:
            return f"no {side} lines"
        if count == 1:
            return f"{side} line {start}"
        return f"{side} lines {start}-{start + count - 1}"
    a, b, c, d = match.groups()
    return f"{name}: {span('before', a, b)}, {span('after', c, d)}"


def changed_hunks(previous, current):
    """Describe the hunks of the current patch that the previous patch
    does not have."""
    seen = {}
    for _, key, _ in hunks(previous):
        seen[key] = seen.get(key, 0) + 1
    changed = []
    for name, key, match in hunks(current):
        if seen.get(key):
            seen[key] -= 1
        else:
            changed.append(describe(name, match))
    return changed


def readme(changes):
    body = """\
# Review materials

- `before/` is a complete copy of the project before the change.
- `after/` is a complete copy of the project after the change.
- `patch.diff` is the change, from `before/` to `after/`.
"""
    if changes:
        body += f"""\
- `{CHANGES}` lists the parts of the patch that are new or
  different since the previous round of review. The rest of the patch
  is the same as in that round.
"""
    body += """
Each tree has its own git repository whose history is one commit
holding the contents of `before/`. In `after/` the change is not
committed, so `git status` and `git diff HEAD` there show it. You may
edit, build and run tests in either tree. Nothing you do here affects
anyone else, so there is no need to copy the trees or apply the patch.

Work only inside this directory. Other copies of the project, including
the directory you started in, are not the versions under review.
"""
    return body


def stage(args):
    source = Source(args.C)
    out = os.path.realpath(args.out)
    if out == source.path or out.startswith(source.path + os.sep):
        raise Failure(f"{out} is inside the worktree being staged")
    if len(set(args.names)) != len(args.names) or any(
            not n or "/" in n or n.startswith(".") for n in args.names):
        raise Failure("reviewer names must be distinct, must not start with"
                      " '.', and must not contain '/'")
    copied = [os.path.normpath(p) for p in args.copy_ignored]
    for rel in copied:
        if os.path.isabs(rel) or rel.split(os.sep)[0] == "..":
            raise Failure(f"--copy-ignored {rel}: not inside the worktree")
        if not os.path.lexists(os.path.join(source.path, rel)):
            raise Failure(f"--copy-ignored {rel}: no such file or directory")
        if not source.ignores(rel):
            raise Failure(f"--copy-ignored {rel}: git does not ignore it")
    record = source.git_path("review-stage.json")
    previous = None
    if args.since_last:
        if not os.path.exists(record):
            raise Failure("--since-last: nothing has been staged from this"
                          " worktree yet")
        try:
            with open(record) as f:
                previous = json.load(f)
            trees = (previous["base"], previous["head"])
        except (ValueError, KeyError, TypeError) as e:
            raise Failure(f"--since-last: cannot read {record}: {e}")
        for tree in trees:
            source.git("cat-file", "-e", tree + "^{tree}")
    if args.base:
        base = source.tree(args.base)
    else:
        try:
            base = source.tree(source.text("merge-base", "HEAD",
                                           "@{upstream}"))
        except Failure as e:
            raise Failure(f"{e}\nName the version before the change with"
                          " --base.")
    try:
        os.mkdir(out)
    except OSError as e:
        raise Failure(f"cannot create {out}: {e.strerror}")
    try:
        scratch = os.path.join(out, ".staging")
        os.mkdir(scratch)
        head = source.tree(args.head) if args.head else \
            source.worktree_tree(scratch)
        if base == head:
            raise Failure("the base and the head are the same, so there is"
                          " nothing to review")
        trees, dirs, changes = build_masters(source, base, head, copied,
                                             scratch)
        only = {v: [p for s, p in changes if s == status]
                for v, status in ((head, b"A"), (base, b"D"))}
        kept = (previous or {}).get("forward", {})
        fresh = [n for n in args.names if n not in kept]
        made = [True, False] * (len(fresh) // 2)
        if len(fresh) % 2:
            made.append(_random.random() < 0.5)
        _random.shuffle(made)
        assignment = {n: kept[n] if n in kept else made.pop()
                      for n in args.names}
        for name, forward in assignment.items():
            before, after = (base, head) if forward else (head, base)
            place = os.path.join(out, name)
            os.mkdir(place)
            build_tree(trees[before], dirs[before],
                       os.path.join(place, "before"), [])
            build_tree(trees[after], dirs[before],
                       os.path.join(place, "after"), only[after])
            with open(os.path.join(place, "patch.diff"), "wb") as f:
                f.write(source.diff(before, after))
            if previous:
                old = (previous["base"], previous["head"])
                old = old if forward else old[::-1]
                listed = changed_hunks(source.diff(*old),
                                       source.diff(before, after))
                with open(os.path.join(place, CHANGES), "w") as f:
                    f.write("".join(c + "\n" for c in listed) if listed
                            else "Nothing in the patch has changed.\n")
            with open(os.path.join(place, "README.md"), "w") as f:
                f.write(readme(previous is not None))
        shutil.rmtree(scratch)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    with open(record, "w") as f:
        json.dump({"base": base, "head": head, "forward": assignment}, f)
    backup = source.git_path("review-stage.patch")
    with open(backup, "wb") as f:
        f.write(source.diff(base, head, "--binary", "--full-index",
                            "--no-textconv"))
    print(f"Staged {base[:12]}..{head[:12]}: "
          + source.text("diff", "--shortstat", base, head))
    for name, forward in assignment.items():
        print(f"  {os.path.join(out, name)}: "
              + ("the change as made" if forward else "the change reversed"))
    print(f"Patch of the change as made: {backup}")
    return assignment


def parse(argv):
    parser = argparse.ArgumentParser(
        description="Stage a change for reviewers who must not be able to"
        " tell which version came first.")
    parser.add_argument("out", help="the directory to create")
    parser.add_argument("names", nargs="+", help="one name per reviewer")
    parser.add_argument("-C", default=".", metavar="WORKTREE",
                        help="the worktree holding the change")
    parser.add_argument("--base", help="the version before the change"
                        " (default: the merge base of HEAD and its upstream)")
    parser.add_argument("--head", help="the version after the change"
                        " (default: the working tree, untracked files"
                        " included)")
    parser.add_argument("--copy-ignored", action="append", default=[],
                        metavar="PATH", help="an ignored file or directory,"
                        " relative to the top of the worktree, to copy into"
                        " both trees as it is (repeatable)")
    parser.add_argument("--since-last", action="store_true",
                        help="list the parts of the patch that changed since"
                        " the last staging from this worktree")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        stage(parse(argv))
    except Failure as e:
        print(f"review-stage: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
