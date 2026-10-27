"""Loaded by every Python process that a longrun command starts, through
PYTHONPATH. Registers a handler that writes every thread's Python stack
to a file when the process receives SIGINFO, then runs the sitecustomize
module this one hides, if there is one: the one that Python finds in the
entries of sys.path after the one this module was loaded from."""

import importlib.machinery
import os
import sys

_dir = os.environ.get("LONGRUN_PYSTACKS")
if _dir and os.path.isdir(_dir):
    import faulthandler
    import signal
    _file = open(os.path.join(_dir, "%d.txt" % os.getpid()), "a")
    faulthandler.register(signal.SIGINFO, file=_file, all_threads=True)

_own = os.path.abspath(os.path.dirname(__file__))
_after = sys.path[sys.path.index(_own) + 1:]
_spec = importlib.machinery.PathFinder.find_spec("sitecustomize", _after)
if _spec is not None:
    importlib._bootstrap._load(_spec)
