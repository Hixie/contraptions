# review-stage

Prepares everything a set of code reviewers needs to review one change,
so that the reviewers run no git commands of their own and cannot tell
which version of the code came first.

Half of the reviewers see the change as it was made. The other half see
it reversed, as if the new code were the original and the change undid
it. Each reviewer judges whether its patch is an improvement. Because
no reviewer knows which way round its patch is, a reviewer cannot
simply assume that the newer code is better.

## Running it

From the worktree holding the change:

```sh
~/dev/contraptions/review-stage/review_stage.py OUT 🦜 🦆
```

`OUT` is a directory to create. It must not already exist, it must be
outside the worktree, and it must be on an APFS volume (the same one as
the worktree, if `--copy-ignored` is used). Each name after it gets a
directory of its own inside `OUT`. The command prints
which reviewers see the change as made and which see it reversed. Give
each reviewer the path of its own directory and nothing about its
orientation. Delete `OUT` once the review is over.

By default the change runs from the merge base of `HEAD` and its
upstream branch to the working tree as it is, including untracked files
that are not ignored, but not untracked nested repositories. Edits to
files marked `assume-unchanged` count; files marked `skip-worktree`
count as the index has them. Two options choose other versions:

- `--base REV` names the version before the change, for example
  `HEAD~1` to review only the last commit.
- `--head REV` names the version after the change, for example `HEAD`
  to leave uncommitted edits out.

`-C WORKTREE` runs the command on another worktree.

`--copy-ignored PATH` copies an ignored file or directory, named
relative to the top of the worktree, into both trees as it is. It can
be given more than once. It is for things tests need whose contents do
not depend on the version, such as a directory of installed
dependencies. Ignored files are otherwise left out, as described below.

## What each reviewer gets

- `before/` and `after/`, copies of the project's files at the two
  versions, each the reviewer's own to edit, build and test in.
- `patch.diff`, the change from `before/` to `after/`, with ordinary
  `a/` and `b/` file names.
- `README.md`, which tells the reviewer what these are and asks it to
  work only inside its directory.
- In later rounds, `changed-since-previous-round.txt`, described below.

Each tree has its own git repository whose history is a single commit
holding the contents of `before/`. In `after/` the change is left
uncommitted, so `git status` and `git diff HEAD` there show it. Files
that `after/` has and `before/` lacks are recorded as intended to be
added, so `git diff HEAD` shows them too. The first git command in each
tree reads every file once, which takes a second or two in a large
project.

The trees hold only the files git tracks, plus any paths named with
`--copy-ignored`. Files the worktree ignores are left out because many
of them are built from the working tree's files and so match only one
of the versions. For example, a Python `__pycache__` directory records
the size of each source file it was compiled from, and would also
remain in a tree from which the change removes the source files.
Anything named with `--copy-ignored` comes from the worktree as it is,
so name only paths that are the same for both versions.

## How the direction is hidden

- The commit in every tree has the same author, committer, date and
  message. Its hash differs only because its contents do.
- Every git directory holds the objects of both versions, in one pack
  ordered by the objects' hashes and with no object stored as a change
  to another. So neither which objects it holds, nor their order, says
  which version its commit is. Its record of changes to its branch has
  the same fixed author and time.
- Each tree's index, git's record of the files it last saw, starts
  with no times or file numbers in it, so it holds nothing from the
  staging.
- The two trees are built the same way. The files both versions share
  are written once and cloned into both, so they share their storage
  and their APFS clone identity. Then each version's other files are
  written into newly allocated storage, in one random order across
  both trees, so the order in which files were written says nothing
  either. Every other step taken once for each version, such as
  writing each git directory's commit, takes the versions in one
  random order too. A change to a `.gitattributes` file can alter how
  every file is written out, so when the change touches one, no files
  count as shared.
- Files and directories that are not shared, and everything in the git
  directories, get one modification and birth time, taken before
  either tree is written. Shared files keep the times of their one
  writing. Every file and directory then gets one access time, later
  than any of the modification times, so that no access time depends
  on when a file was written or read during the staging.
- A few things are written for each reviewer in turn, after the trees
  are cloned: the index, the empty file git stores for files to be
  added, and the modification times of directories, which cloning sets
  to the time of cloning. `before/` is always cloned before `after/`,
  whichever way round the reviewer's patch is.
- Files are written out with the source repository's attributes and
  filters, as a checkout there would write them. Each reviewer's
  repository gets the source repository's `info/exclude` and
  `info/attributes` files and its own settings for line endings,
  filters and diff drivers, so git there treats the files the same
  way.
- Each patch is made by comparing its two versions in the order the
  reviewer sees them, not by reversing the other patch, so its file
  headers read the ordinary way round.
- The command checks that the two trees it clones the reviewers' trees
  from hold exactly the paths of their versions, each a file, an
  executable file or a symbolic link as the version records, and no
  other files, and fails if they do not.

A reviewer can still find the real worktree, which it starts in, and
inspect its history. The reviewer's `README.md` asks it not to.

## Later rounds

After fixing what a review found, stage the change again with
`--since-last`:

```sh
~/dev/contraptions/review-stage/review_stage.py --since-last OUT2 🦉 🦤
```

Each reviewer directory then also has
`changed-since-previous-round.txt`. It lists, by file and line numbers
in both `before/` and `after/`, the hunks of the patch that the previous
staging from this worktree did not have. A hunk that only moved
because lines above it changed is not listed. The list says which parts
of the patch changed, but not on which side, so a reviewer that did not
see the previous round cannot tell the direction from it. A reviewer
that remembers the previous round's patch can, because the fixes land
on the side holding the version as made. Use new reviewers, with new
names, for each round. A reviewer that is named again keeps the
orientation it had, so that a reviewer continued from the previous
round still sees the patch the same way round.

## Files kept in the repository

Each run writes two files into the worktree's git directory. Their
paths are given by `git rev-parse --git-path review-stage.json` and
`git rev-parse --git-path review-stage.patch`.

- `review-stage.json` records the two versions and each reviewer's
  orientation, for `--since-last`.
- `review-stage.patch` is the change as made, in a form `git apply`
  accepts, binary files included. With the default `--head`, it is a
  copy of the working tree's changes, from which the worktree can be
  rebuilt if anything damages it.

When the working tree is staged, the command stores its files' contents
in the repository's object database without referring to them from any
commit or branch. Git's garbage collection deletes such contents once
they are two weeks old (by default), so `--since-last` only works on a
working tree staged within that time.

## Requirements and tests

The command needs macOS, because it clones files with `clonefile(2)`,
which only APFS supports. It also needs git 2.28 or newer and Python
3.9 or newer, with no other dependencies.

Run the tests from this directory:

```sh
python3 -m unittest -v
```
