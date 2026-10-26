"""Loaded by every Python process that a longrun command starts, through
PYTHONPATH. Registers a handler that writes every thread's Python stack
to a file when the process receives SIGINFO, then runs the
sitecustomize module this one hides, if there is one."""

import os
import sys

_dir = os.environ.get("LONGRUN_PYSTACKS")
if _dir and os.path.isdir(_dir):
    import faulthandler
    import signal
    _file = open(os.path.join(_dir, "%d.txt" % os.getpid()), "a")
    faulthandler.register(signal.SIGINFO, file=_file, all_threads=True)

_here = os.path.dirname(os.path.abspath(__file__))
for _entry in sys.path:
    _path = os.path.join(_entry or ".", "sitecustomize.py")
    if os.path.abspath(_entry or ".") != _here and os.path.isfile(_path):
        import runpy
        runpy.run_path(_path, run_name="sitecustomize")
        break
